# Our imports; note that LLM SDKs (openai, anthropic, groq, ollama, gemini) are lazy loaded through the Model class.
from jinja2 import Environment, meta, StrictUndefined, Template   # we use jinja2 for prompt templates
import os                                               # for environment variables
import dotenv                                           # for loading environment variables
import itertools                                        # for flattening list of models
import json                                             # for our jsonparser
import time                                             # for timing our query calls (saved in Response object)
from pydantic import BaseModel
from typing import List, Optional, Type, Union, Literal # for type hints
import instructor                                       # for parsing objects from LLMs
import asyncio										    # for async
from collections import defaultdict						# for defaultdict
from pathlib import Path
import importlib
import ollama	# Still need to import this since we use it to generate the list of models for Ollama.

# set up our environment: dynamically setting the .env location considered best practice for complex projects.
dir_path = Path(__file__).resolve().parent
env_path = dir_path / '.env'
# Load the environment variables
dotenv.load_dotenv(dotenv_path=env_path)
api_keys = {
	'OPENAI_API_KEY': os.getenv("OPENAI_API_KEY"),
	'ANTHROPIC_API_KEY': os.getenv("ANTHROPIC_API_KEY"),
	'GROQ_API_KEY': os.getenv("GROQ_API_KEY"),
	'GOOGLE_API_KEY': os.getenv("GOOGLE_API_KEY")
}
env = Environment(undefined=StrictUndefined)            # # set jinja2 to throw errors if a variable is undefined

class Message(BaseModel):
	"""
	Industry standard, more or less, for messaging with LLMs.
	System roles can have some weirdness (like with Anthropic), but role/content is standard.
	"""
	role: str
	content: str

class Messages(BaseModel):
	"""
	Industry standard, more or less, for messaging with LLMs.
	System roles can have some weirdness (like with Anthropic), but role/content is standard.
	"""
	messages: List[Message]

def is_messages_object(input):
	if not input:
		return False
	try:
		Messages(messages=input)
		return True
	except Exception as e:
		print(e)
		return False

class Chain():
	"""
	How we chain things together.
	Instantiate with:
	- a prompt (a string that is ready for jinja2 formatting),
	- a model (a name of a model (full list of accessible models in Model.models))
	- a parser (a function that takes a string and returns a string)
	Defaults to mistral for model, and empty parser.
	"""
	# Put API keys for convenience across my system.
	api_keys = api_keys
	# Canonical source of models; or if there are new cloud models (fex. Gemini)
	try:
		ollama_models = [m['name'] for m in ollama.list()['models']]
	except:
		ollama_models = []
	models = {
		"ollama": ollama_models,
		"openai": ["gpt-4o","gpt-4-turbo","gpt-3.5-turbo-0125", "gpt-4o-mini", "o1-preview", "o1-mini"],
		"anthropic": ["claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307", "claude-3-5-sonnet-20240620"],
		"google": ["gemini-1.5-pro-latest", "gemini-1.5-flash-latest", "gemini-1.0-pro-latest"],
		"groq": ["llama3-8b-8192", "llama3-70b-8192", "mixtral-8x7b-32768", "gemma-7b-it"],
		"testing": ["polonius"]
	}
	# Ollama is throttled at 2048 token context size by default; let's go with 4096 and set even higher for some models.
	ollama_context_sizes = defaultdict(lambda: 4096) # DefaultDict so that any model not listed here defaults to 2048
	ollama_context_sizes.update({
		"mistral:latest": 8192,
		"llama3.1:latest": 32768,	# Llama3.1 supposedly goes up to 128k; this is an intermediate / performance size.
		"lama3.1:70b-instruct-q2_K": 32768,
		'dolphin-mixtral:latest': 16384,
		'dolphin-mixtral:8x7b': 32768,
	})
	# Silly examples for testing; if you declare a Chain() without inputs, these are the defaults.
	examples = {
		'batch_example': [{'input': 'John Henry'}, {'input': 'Paul Bunyan'}, {'input': 'Babe the Blue Ox'}, {'input': 'Brer Rabbit'}],
		'run_example': {'input': 'John Henry'},
		'model_example': 'mistral:latest',
		'prompt_example': 'sing a song about {{input}}. Keep it under 200 characters.',
		'system_prompt_example': "You're a helpful assistant.",
	}
	
	def update_models() -> None:
		"""
		If you need to update the ollama model list on the fly, use this function.
		"""
		models = [m['name'] for m in ollama.list()['models']]
		Chain.models['ollama'] = models
	
	def standard_repr(object) -> str:
		# sourcery skip: instance-method-first-arg-name
		"""
		Standard for all of my classes; changes how the object is represented when invoked in interpreter.
		Called from all classes related to Chain project (Model, Prompt, Chat, etc.).
		"""
		attributes = ', '.join([f'{k}={repr(v)[:50]}' for k, v in object.__dict__.items()])
		return f"{object.__class__.__name__}({attributes})"
		# Example output: Chain(prompt=Prompt(string='tell me about {{topic}}', format_in, model=Model(model='mistral'), parser=Parser(parser=<function Chain.<lambda> at 0x7f7c5a
	
	def find_variables(self, template: Template) -> set:
		"""
		This function takes a jinja2 template and returns a set of variables; used for setting input_schema of Chain object.
		"""
		throwaway_env = Environment()
		parsed_content = throwaway_env.parse(template)
		return meta.find_undeclared_variables(parsed_content)
	
	def __init__(self, prompt=None, model=None, parser=None):
		if prompt is None:              # if inputs are empty, use the defaults from Model.examples
			prompt = Prompt(Chain.examples['prompt_example'])
		elif isinstance(prompt, str):
			prompt = Prompt(prompt)     # if prompt is a string, turn it into a Prompt object <-- for fast iteration
		if model is None:
			model = Model(Chain.examples['model_example'])
		self.prompt = prompt
		self.model = model
		self.parser = parser
		# Now a little magic within and between the objects
		## Set up input_schema and output_schema
		self.input_schema = self.find_variables(self.prompt.string)  # this is a set
		self.output_schema = {'result'}                         # in the future, we'll allow users to define this, for chaining purposes
	
	def __repr__(self):
		return Chain.standard_repr(self)
	
	def create_messages(system_prompt = examples['system_prompt_example'], input_variables = None) -> list[dict]:
		"""
		Takes a system prompt object (Prompt()) or a string, an optional input object, and returns a list of messages.
		"""
		if isinstance(system_prompt, str):
			system_prompt = Prompt(system_prompt)
		if input_variables:
			messages = [{'role': 'system', 'content': system_prompt.render(input_variables=input_variables)}]
		else:
			messages = [{'role': 'system', 'content': system_prompt.string}]
		return messages
	
	def run(self, input_variables: Union[str, dict] = None, verbose=True, messages = []):
		"""
		Input should be a dict with named variables that match the prompt.
		"""
		# Render our prompt with the input_variables
		if input_variables:
			if isinstance(input_variables, str) and len(self.input_schema) == 1:
				input_variables = {list(self.input_schema)[0]: input_variables}
			prompt = self.prompt.render(input_variables = input_variables)
		else:
			prompt = self.prompt.string
		# Route input; if string, if message
		if messages:
			result = self.run_messages(prompt = prompt, messages = messages, verbose=verbose)
		else:
			result = self.run_completion(prompt = prompt, model = self.model.model, verbose=verbose)
		return result
	
	def run_messages(self, prompt: str, messages, verbose = True):
		"""
		Special version of Chain.run that takes a messages object.
		Converts input + prompt into a message object, appends to messages list, and runs to Model.chat.
		Input should be a dict with named variables that match the prompt.
		"""
		# Add new query to messages list
		message = {'role': 'user', 'content': prompt}
		messages.append(message)
		# Run our query
		time_start = time.time()
		if self.parser:
			result = self.model.query(messages, verbose=verbose, pydantic_model = self.parser.pydantic_model)
			# result = json.dumps(result.__dict__)
		else:
			result = self.model.query(messages, verbose=verbose)
		time_end = time.time()
		duration = time_end - time_start
		# Return a response object
		# Convert result to a string
		assistant_message = {'role': 'assistant', 'content': result}
		messages.append(assistant_message)
		response = Response(content=result, status="success", prompt=prompt, model=self.model.model, duration=duration, messages = messages, variables=input)
		return response
	
	def run_completion(self, prompt: str, model: str, verbose = True):
		"""
		Standard version of Chain.run which returns a string (i.e. a completion).
		Input should be a dict with named variables that match the prompt.
		"""
		time_start = time.time()
		if self.parser:
			result = self.model.query(prompt, verbose=verbose, pydantic_model = self.parser.pydantic_model)
		else:
			result = self.model.query(prompt, verbose=verbose)
		time_end = time.time()
		duration = time_end - time_start
		# Return a response object; in future maybe we want a messages list to be generated by default here.
		response = Response(content=result, status="success", prompt=prompt, model=self.model.model, duration=duration, messages = [], variables=input)
		return response
	
	def run_async(self, input_variables: Union[str, dict] = None, verbose=True):
		"""
		Note: async is currently handled by Model class.
		Didn't make sense to have as a run function given than my main use case is to mass produce prompts and pass a list of them to the query_async function.
		"""
		pass
	
	def batch(self, input_list=[]):
		"""
		Input list is a list of dictionaries.
		"""
		if input_list == []:
			input_list = Chain.examples['batch_example']
		batch_output = []
		for input in input_list:
			print(f"Running batch with input: {input}")
			batch_output.append(self.run(input))
		return batch_output

class Prompt():
	"""
	Generates a prompt.
	Takes a jinja2 ready string (note: not an actual Template object; that's created by the class).
	"""
	
	def __init__(self, template = Chain.examples['prompt_example']):
		self.string = template
		self.template = env.from_string(template)
	
	def __repr__(self):
		return Chain.standard_repr(self)
	
	def render(self, input_variables):
		"""
		takes a dictionary of variables
		"""
		rendered = self.template.render(**input_variables)    # this takes all named variables from the dictionary we pass to this.
		return rendered

class Model():
	"""
	Our basic model class.
	Instantiate with a model name; you can find full list at Model.models.
	This routes to either OpenAI, Anthropic, Google, or Ollama models, in future will have groq.
	There's also an async method which we haven't connected yet (see gpt_async below).
	"""
	_clients = {}	# This is where we store our clients; we don't want to keep creating them. (lazy load implementation)
	
	def is_message(self, obj):
		"""
		This check is a particular input is a Message type (list of dicts).
		Primarily for routing from query to chat.
		Return True if it is a list of dicts, False otherwise.
		"""
		if not isinstance(obj, list):
			return False
		return all(isinstance(x, dict) for x in obj)
	
	def __repr__(self):
		return Chain.standard_repr(self)
	
	def pretty(self, user_input):
		"""
		Truncate input to 150 characters for pretty logging.
		"""
		pretty = user_input.replace('\n', ' ').replace('\t', ' ').strip()
		return pretty[:150]

	def __init__(self, model=Chain.examples['model_example']):
		"""
		Given that gpt and claude model names are very verbose, let users just ask for claude or gpt.
		"""
		# set up a default query for testing purposes
		self.example_query = Prompt(Chain.examples['prompt_example']).render(Chain.examples['run_example'])
		# route any aliases that I've made for specific models
		self.model = self._validate_model(model)
		self._client = None
		self._client_type = self._get_client_type()

	def _validate_model(self, model: str) -> str:
		"""
		This is where you can put in any model aliases you want to support.
		"""
		if model == 'claude':
			self.model = 'claude-3-5-sonnet-20240620'                               # newest claude model as of 6/21/2024
		elif model == 'gpt':
			self.model = 'gpt-4o'                                                   # defaulting to the cheap strong model they just announced
		elif model == 'gpt3':
			self.model = 'gpt-3.5-turbo-0125'                                       # defaulting to the turbo model
		elif model == 'gemini':
			self.model = 'gemini-1.5-pro-latest'                                    # defaulting to the pro (1.0 )model
		elif model == 'groq':
			self.model = 'mixtral-8x7b-32768'                                       # defaulting to the mixtral model
		elif model == "ollama":
			self.model = 'mistral:latest'                                           # defaulting to the latest mistral model
		elif model == "haiku":
			self.model = 'claude-3-haiku-20240307'                                  # haiku model
		elif model == "gpt-mini":
			self.model = 'gpt-4o-mini'											    # defaulting to the mini model
		elif model in list(itertools.chain.from_iterable(Chain.models.values())):   # any other model we support (flattened the list)
			self.model = model
		else:
			ValueError(f"WARNING: Model not found locally: {model}. This may cause errors.")
		return self.model

	def _get_client_type(self):
		"""
		Setting client_type for Model object is necessary for loading the correct client in the query functions.
		"""
		if self.model in Chain.models['openai']:
			return 'openai'
		elif self.model in Chain.models['anthropic']:
			return 'anthropic'
		elif self.model in Chain.models['google']:
			return 'google'
		elif self.model in Chain.models['ollama']:
			return 'ollama'
		elif self.model in Chain.models['groq']:
			return 'groq'
		elif self.model in Chain.models['testing']:
			return 'testing'
		else:
			raise ValueError(f"Model {self.model} not found in Chain.models")

	@classmethod
	def _get_client(cls, client_name):
		"""
		This is our lazy load implementation; heavy SDKs like OpenAI and Anthropic are only loaded when needed.
		At class level because we want to share the clients across all instances of the class / in same session.
		"""
		if client_name not in cls._clients:
			if client_name == 'openai':
				OpenAI = importlib.import_module('openai').OpenAI
				instructor = importlib.import_module('instructor')
				cls._clients[client_name] = instructor.from_openai(OpenAI(api_key=api_keys['OPENAI_API_KEY']))
			elif client_name == 'anthropic':
				Anthropic = importlib.import_module('anthropic').Anthropic
				instructor = importlib.import_module('instructor')
				cls._clients[client_name] = instructor.from_anthropic(Anthropic(api_key=api_keys['ANTHROPIC_API_KEY']))
			elif client_name == 'google':
				genai = importlib.import_module('google.generativeai')
				instructor = importlib.import_module('instructor')
				genai.configure(api_key=api_keys["GOOGLE_API_KEY"])
				cls._clients[client_name] = genai
			elif client_name == 'groq':
				Groq = importlib.import_module('groq').Groq
				cls._clients[client_name] = Groq(api_key=api_keys['GROQ_API_KEY'])
			elif client_name == 'ollama':
				ollama = importlib.import_module('ollama')
				instructor = importlib.import_module('instructor')
				cls._clients[client_name] = ollama
			else:
				raise ValueError(f"Unknown client type: {client_name}")
		
		return cls._clients[client_name]
	
	def query(self, input: Union[str, list], verbose: bool=True, pydantic_model = None) -> Union[BaseModel, str, List]:
		if self._client is None:
			self._client = self._get_client(self._client_type)
		# Route to the correct query function based on client type
		match self._client_type:
			case 'openai':
				return self._query_openai(input, verbose, pydantic_model)
			case 'anthropic':
				return self._query_anthropic(input, verbose, pydantic_model)
			case 'google':
				return self._query_google(input, verbose, pydantic_model)
			case 'ollama':
				return self._query_ollama(input, verbose, pydantic_model)
			case 'groq':
				return self._query_groq(input, verbose, pydantic_model)
			case 'testing':
				return self._query_testing(input, verbose, pydantic_model)
			case _:
				raise ValueError("Error instantiating client.")
	
	def _query_testing(self, user_input):
		"""
		Fake model for testing purposes.
		"""
		_ = user_input
		response = """
My liege, and madam, to expostulate /
What majesty should be, what duty is, / 
Why day is day, night night, and time is time, / 
Were nothing but to waste night, day and time. / 
herefore, since brevity is the soul of wit, / And tediousness the limbs and outward flourishes, /
I will be brief: your noble son is mad: /
Mad call I it; for, to define true madness, /
What is't but to be nothing else but mad? / But let that go.
		"""
		return response
	
	def _query_openai(self, input: Union[str, list], verbose: bool=True, pydantic_model: Optional[Type[BaseModel]] = None) -> Union[BaseModel, str]:
		"""
		Handles all synchronous requests from OpenAI's models.
		Possibilities:
		- pydantic object not provided, input is string -> return string
		- pydantic object provided, input is string -> return pydantic object
		"""
		if verbose:
			print(f"Model: {self.model}   Query: " + self.pretty(str(input)))
		if isinstance(input, str):
			input = [{"role": "user", "content": input}]
		elif is_messages_object(input):
			input = input
		else:
			raise ValueError(f"Input not recognized as a valid input type: {type:input}: {input}")
		# call our client
		response = self._client.chat.completions.create(
			# model=self.model,
			model = self.model,
			response_model = pydantic_model,
			messages = input
		)
		if pydantic_model:
			return response
		else:
			return response.choices[0].message.content
	
	def _query_anthropic(self, input: Union[str, list], verbose: bool=True, pydantic_model = None) -> Union[BaseModel, str]:
		"""
		Handles all synchronous requests from Anthropic's models.
		Possibilities:
		- pydantic object not provided, input is string -> return string
		- pydantic object provided, input is string -> return pydantic object
		Anthropic is quirky about system messsages (The Messages API accepts a top-level `system` parameter, not "system" as an input message role.)
		"""
		# Anthropic requires a system variable
		if verbose:
			print(f"Model: {self.model}   Query: " + self.pretty(str(input)))
		system = ""
		if isinstance(input, str):
			input = [{"role": "user", "content": input}]
		elif is_messages_object(input):
			input = input
			# This is anthropic quirk; we remove the system message and set it as a query parameter.
			if input[0]['role'] == 'system':
				system = input[0]['content']
				input = input[1:]
		else:
			raise ValueError(f"Input not recognized as a valid input type: {type:input}: {input}")
		# set max_tokens based on model
		if self.model == "claude-3-5-sonnet-20240620":
			max_tokens = 8192
		else:
			max_tokens = 4096
		# call our client
		response = self._client.chat.completions.create(
			# model = self.model,
			model = self.model,
			max_tokens = max_tokens,
			max_retries = 0,
			system = system,
			messages = input,
			response_model = pydantic_model,
		)
		# only two possibilities here
		if pydantic_model:
			return response
		else:
			return response.content[0].text
	
	def _query_ollama(self, input: Union[str, list], verbose: bool=True, pydantic_model: Optional[Type[BaseModel]] = None) -> Union[BaseModel, str]:
		"""
		Handles all synchronous requests from Ollama models.	
		DOES NOT SUPPORT PYDANTIC MODELS, SINCE I CANNOT FIGURE HOW TO SET CTX FOR INSTRUCTOR.
		num_ctx = context window size, which is set in Chain.ollama_context_sizes.
		"""
		if verbose:
			print(f"Model: {self.model}   Query: " + self.pretty(str(input)))
		if isinstance(input, str):
			input = [{"role": "user", "content": input}]
		elif is_messages_object(input):
			input = input
		else:
			raise ValueError(f"Input not recognized as a valid input type: {type:input}: {input}")
		# call our client
		response = ollama.chat(
			model = self.model,
			messages = input,
			options = {'num_ctx': Chain.ollama_context_sizes[self.model]}
		)
		if pydantic_model:
			print("Pydantic model not supported for Ollama models currently.")
		else:
			return response['message']['content']
	
	def _query_google(self, input: Union[str, list], verbose: bool=True, pydantic_model: Optional[Type[BaseModel]] = None) -> Union[BaseModel, str]:
		"""
		Google doesn't take message objects, apparently. (or it's buried in their documentation)
		"""
		if verbose:
			print(f"Model: {self.model}   Query: " + self.pretty(str(input)))
		if isinstance(input, str):
			input = input
		elif is_messages_object(input):
			input = input['content']
		else:
			raise ValueError(f"Input not recognized as a valid input type: {type:input}: {input}")
		# call our client	
		gemini_client_model = instructor.from_gemini(
			client=self._client.GenerativeModel(
				model_name = self.model,
			),
			mode=instructor.Mode.GEMINI_JSON,
		)
		response = gemini_client_model.chat.completions.create(
			messages=[
				{
					"role": "user",
					"content": input,
				}
			],
			response_model = pydantic_model,
		)
		if pydantic_model:
			return response
		else:
			return response.candidates[0].content.parts[0].text
	
	def _query_groq(self, input: Union[str, list], verbose: bool=True, pydantic_model: Optional[Type[BaseModel]] = None) -> Union[BaseModel, str]:
		"""
		TBD: Not sure if Instructor plays nice with groq.
		"""
		pass
	
	async def query_openai_async(self, prompt: Union[str, list], verbose: bool=True, model: str = "gpt-3.5-turbo-0125", pydantic_model: Optional[Type[BaseModel]] = None, request_num: int = None) -> Union[BaseModel, str]:
		"""
		Handles all asynchronous requests from OpenAI's models.
		Possibilities:
		- pydantic object not provided, input is string -> return string
		- pydantic object provided, input is string -> return pydantic object
		"""
		if verbose:
			print(f"Model: {self.model}   Query: " + self.pretty(str(input)))
		if isinstance(prompt, str):
			prompt = [{"role": "user", "content": prompt}]
		elif is_messages_object(prompt):
			prompt = prompt
		else:
			raise ValueError(f"Prompt not recognized as a valid prompt type: {type(prompt)}: {prompt}")
		
		print(f"Sending request #{request_num} to {model}...") if verbose else None
		# call our client
		response = await async_client_openai.chat.completions.create(
			model = model,
			response_model = pydantic_model,
			messages = prompt
		)
		
		print(f"Received response #{request_num} from {model}") if verbose else None
		
		if pydantic_model:
			return response  # Directly returning the response for pydantic processing
		else:
			if hasattr(response, 'choices'):
				return response.choices[0].message.content
			else:
				print("Response structure:", response)  # Debugging line to inspect the structure
				raise AttributeError("Response object does not have 'choices'")
	
	async def query_anthropic_async(self, prompt: Union[str, list], verbose: bool=True, model: str = "claude-3-5-sonnet-20240620", pydantic_model: Optional[Type[BaseModel]] = None, request_num: int = None) -> Union[BaseModel, str]:
		"""
		Handles all asynchronous requests from Anthropic's models using Instructor.
		Possibilities:
		- pydantic object not provided, input is string -> return string
		- pydantic object provided, input is string -> return pydantic object
		Defaults to 3.5 Sonnet, not Haiku because when we use Claude, we are wanting performance.
		"""
		if isinstance(prompt, str):
			prompt = [{"role": "user", "content": prompt}]
		elif is_messages_object(prompt):
			prompt = prompt
		else:
			raise ValueError(f"Prompt not recognized as a valid prompt type: {type(prompt)}: {prompt}")
		
		print(f"Sending request #{request_num} to {model}...") if verbose else None
		
		try:
			if pydantic_model:
				response = await async_client_anthropic.chat.completions.create(
					model=model,
					max_tokens=2000,
					messages=prompt,
					response_model=pydantic_model
				)
				return response
			else:
				# Use a simple string response model when no Pydantic model is provided
				class StringResponse(BaseModel):
					content: str

				response = await async_client_anthropic.chat.completions.create(
					model=model,
					max_tokens=2000,
					messages=prompt,
					response_model=StringResponse
				)
				return response.content
		
		except Exception as e:
			print(f"Error in request #{request_num}: {str(e)}")
			raise

		finally:
			print(f"Received response #{request_num} from {model}") if verbose else None

	async def run_multiple_extracts(self, prompts: list[str], pydantic_model: Optional[Type[BaseModel]] = None, verbose: bool = True, model = "gpt") -> list[Union[BaseModel, str]]:
		tasks = []
		for i, p in enumerate(prompts, start = 1):
			print(f"Preparing task #{i} of {len(prompts)}...") if verbose else None
			if model == "gpt":
				tasks.append(self.query_openai_async(p, pydantic_model = pydantic_model, request_num = i, verbose = verbose))
			elif model == "claude":
				tasks.append(self.query_anthropic_async(p, pydantic_model = pydantic_model, request_num = i, verbose = verbose))
		print(f"Running {len(prompts)} tasks concurrently...") if verbose else None
		start_time = time.time()
		results = await asyncio.gather(*tasks)  # Run them concurrently
		end_time = time.time()
		print(f"All {len(prompts)} tasks completed in {end_time - start_time:.2f} seconds.") if verbose else None
		return results
	
	def run_async(self, prompts: list[str], pydantic_model: Optional[Type[BaseModel]] = None, verbose: bool = True, throttle = 20, model = Literal["gpt","claude"]) -> list[Union[BaseModel, str]]:
		"""
		Example of how to run multiple extracts asynchronously.
		For now, model is either gpt or claude. (Enum)
		"""
		if throttle > 0:
			if len(prompts) > throttle:
				raise ValueError("You've requested more than 50 prompts; throwing an error to spare you bank account.")
		results = asyncio.run(self.run_multiple_extracts(prompts, pydantic_model = pydantic_model, verbose = verbose, model = model))
		return results

class Parser():
	"""
	Parser class for use with Instructor and Pydantic models.
	"""
	def __init__(self, pydantic_model):
		self.pydantic_model = pydantic_model

	def __repr__(self):
		return f"Parser(pydantic_model={self.pydantic_model.__name__})"

class Response():
	"""
	Simple class for responses.
	A string isn't enough for debugging purposes; you want to be able to see the prompt, for example.
	Should read content as string when invoked as such.
	TO DO: have chains pass a log from response to response (containing history of conversation).
	"""
	
	def __init__(self, content = "", status = "N/A", prompt = "", model = "", duration = 0.0, messages = [], variables = {}):
		self.content = content
		self.status = status
		self.prompt = prompt
		self.model = model
		self.duration = duration
		self.messages = messages
		self.variables = variables
	
	def __repr__(self):
		return Chain.standard_repr(self)
	
	def __len__(self):
		"""
		We want to be able to check the length of the content.
		"""
		return len(self.content)
	
	def __str__(self):
		"""
		We want to pass as string when possible.
		Allow json objects (dict) to be pretty printed.
		"""
		if isinstance(self.content, dict):
			return json.dumps(self.content, indent=4)
		elif isinstance(self.content, list):
			return str(self.content)
		else:
			return self.content
	
	def __add__(self, other):
		"""
		We this to be able to concatenate with other strings.
		"""
		if isinstance(other, str):
			return str(self) + other
		return NotImplemented

class Chat():
	"""
	My first implementation of a chatbot.
	"""
	def __init__(self, model=Chain.examples['model_example'], system_prompt=Chain.examples["system_prompt_example"]):
		if isinstance(model, str):
			self.model = Model(model)
		else:
			self.model = model
		self.system_prompt = system_prompt
	
	def __repr__(self):
		return Chain.standard_repr(self)
	
	def chat(self, messages: Messages = []):
		"""
		Chat with the model.
		"""
		if messages:
			messages = messages
		else:
			messages = [{'role': 'system', 'content': self.system_prompt}]
		print("Let's chat! Type '/exit' to leave.")
		while True:
			# handle annoying Claude exception (they don't accept system prompts)
			if self.model.model in Chain.models['anthropic']:
				if messages[0]['role'] == 'system':
					messages[0]['role'] = 'user'
					messages.append({'role': 'assistant', 'content': 'OK, I will follow that as my system message for this conversation.'})
			# grab user input
			user_input = input("You: ")
			new_system_prompt, new_model = "", ""       # reset these each time
			if user_input[:12] == "/set system ":       # because match/case doesn't do wildcards or regex.
				new_system_prompt = user_input[12:]
				user_input = "/set system"
			if user_input[:11] == "/set model ":        # because match/case doesn't do wildcards or regex.
				new_model = user_input[11:]
				user_input = "/set model"
			match user_input:
				case "/exit":
					break
				case "/clear":
					messages = [{'role': 'system', 'content': self.system_prompt}]
					continue
				case "/show system":
					print('============================\n' + 
						self.system_prompt +
						'\n============================\n')
					continue
				case "/show model":
					print(self.model.model)
					continue
				case "/show models":
					print(Chain.models)
					continue
				case "/show messages":
					print('============================\n' + 
						'\n\n'.join([str(m) for m in messages]) +
						'\n============================\n')
					continue
				case "/set system":
					if not new_system_prompt:
						print("You need to enter a system prompt.")
					else:
						self.system_prompt = new_system_prompt
						messages = [{'role': 'system', 'content': self.system_prompt}]
					continue
				case "/set model":
					if not new_model:
						print("You need to enter a model.")
					else:
						try:
							self.model = Model(new_model)
							print(f"Model set to {new_model}. It may take a while to load after your next message.")
						except ValueError:
							print(f"Model not found: {new_model}")
					continue
				case "/help":
					print("""
============================
Commands:
/exit: leave the chat
/clear: clear the chat history
/show system: show the system prompt
/show model: show the current model
/show models: show all available models
/show messages: show the chat history
/set system: set the system prompt
/set model: set the model
============================
				""").strip()
					continue
				case _:
					pass
			messages.append({"role": "user", "content": user_input})
			response = self.model.query(messages)
			messages.append({"role": "assistant", "content": response})
			print(f"Model: {response}")

