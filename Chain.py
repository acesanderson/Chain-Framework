"""
This is me running my own framework, called Chain.
A link is an object that takes the following:
- a prompt (a jinja2 template)
- a model (a string)
- an output (a dictionary)

Next up:
x - define input_schema (created backwards from jinja template (using find_variables on the original string))
x - allow user to edit input_schema
x - define output_schema (default is just "result", but user can define this)
x - add batch function
x - do more validation
 x - should throw an error if input is not a dictionary with the right schema
x - edit link.run so that it could take a single string if needed (just turn it into dict in the method)
x - eidt link.__init__ so that you can just enter a string to initialize as well
    i.e. instead of topic_chain = Chain(Prompt(topic_prompt)), can you just have Chain(topic_prompt)
    this would enable fast iteration
x - add default parsers to Parser class
x - add gemini models
x - handle messages
- add regex parser (takes a pattern)
- allow temperature setting for Model
- Base class is serial, there will be a parallel extension that leverages async
- a way to chain these together with pipes
- add an 'empty' model that just returns the input (converting dicts to strings), for tracing purposes
 - similarly, adding a "tracing" flag that logs all inputs and outputs throughout the process
- consider other format types like [langchain's](https://github.com/langchain-ai/langchain/blob/master/libs/langchain/langchain/output_parsers/format_instructions.py)
"""

# all our packages
from jinja2 import Environment, meta, StrictUndefined   # we use jinja2 for prompt templates
from openai import OpenAI                               # GPT
import google.generativeai as client_gemini             # Google's models
from openai import AsyncOpenAI                          # for async; not implemented yet
from anthropic import Anthropic                         # claude
import ollama                                           # local llms
import re                                               # for regex
import os                                               # for environment variables
import dotenv                                           # for loading environment variables
import itertools                                        # for flattening list of models
import json                                             # for our jsonparser
import ast                                              # for our list parser ("eval" is too dangerous)
import time                                             # for timing our query calls (saved in Response object)
# set up our environment
dotenv.load_dotenv()
client_openai = OpenAI(api_key = os.getenv("OPENAI_API_KEY"))
client_anthropic = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
client_openai_async = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
client_gemini.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
env = Environment(undefined=StrictUndefined)            # # set jinja2 to throw errors if a variable is undefined

class Chain():
    """
    How we chain things together.
    Instantiate with:
    - a prompt (a string that is ready for jinja2 formatting),
    - a model (a name of a model (full list of accessible models in Model.models))
    - a parser (a function that takes a string and returns a string)
    Defaults to mistral for model, and empty parser.
    """
    # Canonical source of models; update that if installing more ollama models, or if there are new cloud models (fex. Gemini)
    models = {
        "ollama": ['qwen:32b', 'qwen:7b', 'zephyr:latest', 'command-r-plus:latest', 'command-r:latest', 'nous-hermes2:latest', 'mixtral:latest', 'llava:latest', 'gemma:7b', 'gemma:2b', 'solar:latest', 'starling-lm:latest', 'neural-chat:latest', 'mistral:latest', 'phi3:latest', 'phi:latest', 'llama3:70b', 'llama3:latest'],
        "openai": ["gpt-4o","gpt-4-turbo","gpt-3.5-turbo-0125"],
        "anthropic": ["claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307"],
        "google": ["gemini-1.5-pro-latest", "gemini-1.5-flash-latest", "gemini-pro"],
        "testing": ["polonius"]
    }
    # Silly examples for testing; if you declare a Chain() without inputs, these are the defaults.
    examples = {
        'batch_example': [{'input': 'John Henry'}, {'input': 'Paul Bunyan'}, {'input': 'Babe the Blue Ox'}, {'input': 'Brer Rabbit'}],
        'run_example': {'input': 'John Henry'},
        'model_example': 'mistral',
        'parser_example': lambda x: x,
        'prompt_example': 'sing a song about {{input}}. Keep it under 200 characters.'
    }
    
    def update_models():
        """
        If you need to update the ollama model list on the fly, use this function.
        """
        models = [m['name'] for m in ollama.list()['models']]
        Chain.models['ollama'] = models
    
    def find_variables(self, template):    
        """
        This function takes a jinja2 template and returns a set of variables; used for setting input_schema of Chain object.
        """
        throwaway_env = Environment()
        parsed_content = throwaway_env.parse(template)
        variables = meta.find_undeclared_variables(parsed_content)
        return variables

    def __init__(self, prompt=None, model=None, parser=None):
        if prompt is None:              # if inputs are empty, use the defaults from Model.examples
            prompt = Prompt(Chain.examples['prompt_example'])
        elif isinstance(prompt, str):
            prompt = Prompt(prompt)     # if prompt is a string, turn it into a Prompt object <-- for fast iteration
        if model is None:
            model = Model(Chain.examples['model_example'])
        if parser is None:
            parser = Parser(Chain.examples['parser_example'])
        self.prompt = prompt
        self.model = model
        self.parser = parser
        # Now a little magic within and between the objects
        ## Set up input_schema and output_schema
        self.input_schema = self.find_variables(self.prompt.string)  # this is a set
        self.output_schema = {'result'}                         # in the future, we'll allow users to define this, for chaining purposes
        ## Now add our format instructions from our parser to the prompt
        self.prompt.format_instructions = self.parser.format_instructions
    
    def __repr__(self):
        """
        Standard for all of my classes; changes how the object is represented when invoked in interpreter.
        """
        attributes = ', '.join([f'{k}={repr(v)[:50]}' for k, v in self.__dict__.items()])
        return f"{self.__class__.__name__}({attributes})"
        # Example output: Chain(prompt=Prompt(string='tell me about {{topic}}', format_in, model=Model(model='mistral'), parser=Parser(parser=<function Chain.<lambda> at 0x7f7c5a
    
    def run(self, input=None, parsed = True):
        """
        Input should be a dict with named variables that match the prompt.
        Chains are parsed by default, but you can turn this off if you want to see the raw output for debugging purposes.
        """
        if input is None:
            input = Chain.examples['run_example']
        # allow users to just put in one string if the prompt is simple <-- for fast iteration
        if isinstance(input, str) and len(self.input_schema) == 1:
            input = {list(self.input_schema)[0]: input}
        prompt = self.prompt.render(input=input)
        time_start = time.time()
        result = self.model.query(prompt)
        time_end = time.time()
        duration = time_end - time_start
        if parsed:
            result = self.parser.parse(result)
        # Return a response object
        response = Response(content=result, status="success", prompt=prompt, model=self.model.model, duration=duration)
        return response
    
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
        self.format_instructions = "" # This starts as empty; gets changed if the Chain object has a parser with format_instructions
        self.template = env.from_string(template)
    
    def __repr__(self):
        """
        Standard for all of my classes; changes how the object is represented when invoked in interpreter.
        """
        attributes = ', '.join([f'{k}={repr(v)[:50]}' for k, v in self.__dict__.items()])
        return f"{self.__class__.__name__}({attributes})"
    
    def render(self, input):
        """
        takes a dictionary of variables
        """
        rendered = self.template.render(**input)    # this takes all named variables from the dictionary we pass to this.
        rendered += self.format_instructions
        return rendered

class Model():
    """
    Our basic model class.
    Instantiate with a model name; you can find full list at Model.models.
    This routes to either OpenAI or Ollama models, in future will have Claude, Gemini.
    There's also an async method which we haven't connected yet (see gpt_async below).
    """
    
    def is_message(self, obj):
        """
        This check is a particular input is a Message type (list of dicts).
        Primarily for routing from query to chat.
        Return True if it is a list of dicts, False otherwise.
        """
        if not isinstance(obj, list):
            return False
        return all(isinstance(x, dict) for x in obj)
    
    def __init__(self, model=Chain.examples['model_example']):
        """
        Given that gpt and claude model names are very verbose, let users just ask for claude or gpt.
        """
        if model == 'claude':
            self.model = 'claude-3-opus-20240229'                                   # we're defaulting to The Beast model; this is a "finisher"
        elif model == 'gpt':
            self.model = 'gpt-4o'                                                   # defaulting to the cheap strong model they just announced
        elif model == 'gemini':
            self.model = 'gemini-pro'                                               # defaulting to the pro (1.0 )model
        elif model in list(itertools.chain.from_iterable(Chain.models.values())):   # any other model we support (flattened the list)
            self.model = model
        else:
            raise ValueError(f"Model not found: {model}")
    
    def __repr__(self):
        """
        Standard for all of my classes; changes how the object is represented when invoked in interpreter.
        """
        attributes = ', '.join([f'{k}={repr(v)[:50]}' for k, v in self.__dict__.items()])
        return f"{self.__class__.__name__}({attributes})"
    
    def query(self, user_input):
        """
        Sorts model to either cloud-based (gpt, claude), ollama, or returns an error.
        """
        if self.is_message(user_input):              # if this is a message, we use chat function instead of query.
            return self.chat(user_input)
        elif self.model in Chain.models['openai']:
                return self.query_gpt(user_input)
        elif self.model in Chain.models['anthropic']:
            return self.query_claude(user_input)
        elif self.model in Chain.models['ollama']:
            return self.query_ollama(user_input)
        elif self.model in Chain.models['google']:
            return self.query_gemini(user_input)
        elif self.model == 'polonius':
            return self.query_polonius(user_input)
        else:
            return f"Model not found: {self.model}"
    
    def pretty(self, user_input):
        """
        Truncate input to 150 characters for pretty logging.
        """
        pretty = re.sub(r'\n|\t', '', user_input).strip()
        return pretty[:150]
    
    def query_ollama(self, user_input):
        """
        Queries local models.
        """
        response = ollama.chat(
            model=self.model,
            messages=[
                {
                'role': 'user',
                'content': user_input,
                },
            ]
        )
        print(f"{self.model}: {self.pretty(user_input)}")
        return response['message']['content']
    
    def query_gpt(self, user_input):
        """
        Queries OpenAI models.
        There's a parallel function for async (gpt_async)
        """
        response = client_openai.chat.completions.create(
            model = self.model,
            messages = [{"role": "user", "content": user_input}]
        )
        print(f"{self.model}: {self.pretty(user_input)}")
        return response.choices[0].message.content
    
    async def query_gpt_async(self, user_input):
        """
        Async version of gpt call; wrap the function call in asyncio.run()
        """
        response = await client_openai_async.chat.completions.create(
            model = self.model,
            messages = [{"role": "user", "content": user_input}]
        )
        print(f"{self.model}: '{self.pretty(user_input)}'")
        return response.choices[0].message.content
    
    def query_claude(self, user_input):
        """
        Queries anthropic models.
        """
        response = client_anthropic.messages.create(
            max_tokens=1024,
            model = self.model,
            messages = [{"role": "user", "content": user_input}]
        )
        print(f"{self.model}: {self.pretty(user_input)}")
        return response.content[0].text
    
    def query_gemini(self, user_input):
        """
        Queries Google's models.
        """
        gemini_model = client_gemini.GenerativeModel(self.model)
        response = gemini_model.generate_content(user_input)
        print(f"{self.model}: {self.pretty(user_input)}")
        return response.candidates[0].content.parts[0].text
    
    def query_polonius(self, user_input):
        """
        Fake model for testing purposes.
        """
        _ = user_input
        response = """My liege, and madam, to expostulate / What majesty should be, what duty is, / Why day is day, night night, and time is time, / Were nothing but to waste night, day and time. / Therefore, since brevity is the soul of wit, / And tediousness the limbs and outward flourishes, / I will be brief: your noble son is mad: / Mad call I it; for, to define true madness, / What is't but to be nothing else but mad? / But let that go."""
        return response
    
    def chat(self, messages):
        """
        Handle messages (these are lists of dicts).
        Sorts model to either cloud-based (gpt, claude), ollama, or returns an error.
        """
        if self.model in Chain.models['openai']:
            return self.chat_gpt(messages)
        elif self.model in Chain.models['anthropic']:
            return self.chat_claude(messages)
        elif self.model in Chain.models['ollama']:
            return self.chat_ollama(messages)
        elif self.model in Chain.models['google']:
            return self.chat_gemini(messages)
        elif self.model == 'polonius':
            return self.query_polonius(messages)
        else:
            return f"Model not found: {self.model}"
    
    def chat_gpt(self, messages):
        """
        Chat with OpenAI models.
        """
        response = client_openai.chat.completions.create(
            model = self.model,
            messages = messages
        )
        return response.choices[0].message.content
    
    def chat_ollama(self, messages):
        """
        Queries local models.
        """
        response = ollama.chat(
            model=self.model,
            messages=messages
        )
        return response['message']['content']
    
    def chat_claude(self, messages):
        """
        Queries anthropic models.
        """
        response = client_anthropic.messages.create(
            max_tokens=1024,
            model = self.model,
            messages = messages
        )
        return response.content[0].text
    
    def chat_gemini(self, messages):
        """
        Queries Google's models.
        """
        # Gemini uses a different schema than Claude or OpenAI, annoyingly.
        for message in messages:
        # Change 'assistant' to 'model' if the role is 'assistant'
            if message['role'] == 'assistant':
                message['role'] = 'model'
            # Replace 'content' key with 'parts' and put the value inside a list
            if 'content' in message:
                message['parts'] = [message.pop('content')]
        gemini_model = client_gemini.GenerativeModel(self.model)
        response = gemini_model.generate_content(messages)
        return response.candidates[0].content.parts[0].text

class Parser():
    """
    Our basic parser class.
    Takes a function and applies it to output.
    At its most basic, it just validates the output.
    For more sophisticated uses (like json), it will also convert the output.
    Todo: have it append instructions to the actual prompt. ("format_instructions" like in langchain)
    """
    def string_parser(output):
        """
        Validates whether output is a string.
        """
        print(output)
        if isinstance(output, str):
            return input
        else:
            raise TypeError("Expected a string, but got a different type")
    
    def json_parser(output):
        """
        Converts string to json object.
        """
        print(output)
        try:
            return json.loads(output)
        except:
            raise ValueError("Could not convert to json")
    
    def list_parser(output):
        """
        Converts string to list, assuming that the string is well-formed Python list syntax.
        This is VERY finicky; tried my best with the format_instructions.
        """
        print(output)
        try:
            return ast.literal_eval(output)
        except:
            raise ValueError("Could not convert to list")
    
    parsers = {
        "str": {
            "function": string_parser,
            "format_instructions": ""
        },
        "json": {
            "function": json_parser,
            "format_instructions": """
                Return your answer as a well-formed json object. Only return the json object; nothing else.
                Do not include any formatting like "```json" or "```" around your answer. I will be parsing this with json.loads().
                """},
        "list": {
            "function": list_parser,
            "format_instructions": """
                Return your answer as a sort of Python list, though with a back slash and a double quote (\") around each item,
                like this: [\"item1\", \"item2\", \"item3\"]. It is important to escape the double quotes so that we can parse it properly.
                Only return the list; nothing else. I will be using python ast.literal_eval() to parse this.
                """},
        "curriculum_parser": {
            "function": json_parser,
            "format_instructions": """
                Return your answer as a json object with this structure (this is just an example):
                {
                    "topic": "ux design",
                    "curriculum": [
                        {
                            "module_number" : 1,
                            "topic" : "topic",
                            "rationale" : "rationale",
                            "description" : "description",
                        }
                        {
                            "module_number" : 2,
                            "topic" : "topic",
                            "rationale" : "rationale",
                            "description" : "description",
                        }
                        {
                            "module_number" : 3,
                            "topic" : "topic",
                            "rationale" : "rationale",
                            "description" : "description",
                        }
                    ]
                }

                Return your answer as a well-formed json object. Only return the json object; nothing else.
                Do not include any formatting like "```json" or "```" around your answer. I will be parsing this with json.loads().
                """}
        }
    
    def __init__(self, parser = "str", format_instructions = ""):
        """
        User can set parser to set of pre-defined parsers (in Parser.parsers) or a custom parser (a function that takes a string).
        Default parser is 'str'. Will throw an error if it doesn't get string (that would be a problem!)
        'str' has empty format_instructions; defaults are added if you use a parser from Parser.parsers.
        User can also define their own format instructions.
        When instantiating a Chain object, the format_instructions from the parser are added to the prompt, and included after Prompt.render.
        """
        if parser in Parser.parsers:
            self.parser = Parser.parsers[parser]['function']
            self.format_instructions = Parser.parsers[parser]['format_instructions']
        elif callable(parser):
            self.parser = parser
            self.format_instructions = format_instructions
        else: 
            raise ValueError(f"Parser is not recognized: {parser}")
    
    def __repr__(self):
        """
        Standard for all of my classes; changes how the object is represented when invoked in interpreter.
        """
        attributes = ', '.join([f'{k}={repr(v)[:50]}' for k, v in self.__dict__.items()])
        return f"{self.__class__.__name__}({attributes})"
    
    def parse(self, output):
        return self.parser(output)

class Response():
    """
    Simple class for responses.
    A string isn't enough for debugging purposes; you want to be able to see the prompt, for example.
    Should read content as string when invoked as such.
    TO DO: have chains pass a log from response to response (containing history of conversation).
    """
    
    def __init__(self, content = "", status = "N/A", prompt = "", model = "", duration = 0.0):
        self.content = content
        self.status = status
        self.prompt = prompt
        self.model = model
        self.duration = duration
    
    def __repr__(self):
        """
        Standard for all of my classes; changes how the object is represented when invoked in interpreter.
        """
        attributes = ', '.join([f'{k}={repr(v)[:50]}' for k, v in self.__dict__.items()])
        return f"{self.__class__.__name__}({attributes})"
    
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
    def __init__(self, model='mistral'):
        self.model = Model(model)
    
    def __repr__(self):
        """
        Standard for all of my classes; changes how the object is represented when invoked in interpreter.
        """
        attributes = ', '.join([f'{k}={repr(v)[:50]}' for k, v in self.__dict__.items()])
        return f"{self.__class__.__name__}({attributes})"
    
    def chat(self):
        """
        Chat with the model.
        """
        system_prompt = "You're a helpful assistant."
        messages = [{'role': 'system', 'content': system_prompt}]
        print("Let's chat! Type 'exit' to leave.")
        while True:
            user_input = input("You: ")
            match user_input:
                case "exit":
                    break
                case "/show system":
                    print('============================\n' + 
                        system_prompt +
                        '\n============================\n')
                    continue
                case "/show model":
                    print(self.model.model)
                    continue
                case "/show messages":
                    print('============================\n' + 
                        '\n\n'.join([str(m) for m in messages]) +
                        '\n============================\n')
                    continue
                case "/help":
                    print("""
                        Type 'exit' to leave the chat.
                        Type '/show system' to see the system prompt.
                        Type '/show model' to see the model.
                        Type '/show messages' to see the conversation.
                        """)
                    continue
                case _:
                    pass
            messages.append({"role": "user", "content": user_input})
            response = self.model.query(messages)
            messages.append({"role": "assistant", "content": response})
            print(f"Model: {response}")

