from Chain import Model, Chain, Prompt, Parser
from pydantic import BaseModel                          # for our input_schema and output_schema; starting with List Parser.
from typing import List, Optional, Type, Union          # for type hints
import json

class Example_List(BaseModel):
	examples: List[str]




# if __name__ == "__main__":
	# print("OLLAMA\n====================================")
	# model = Model('ollama')
	# print(model.query_ollama("sing a sing about john henry", model="mistral:latest"))
	# print("ANTHROPIC\n====================================")
	# model = Model('claude')
	# response = model.query_anthropic("Name ten mammals", pydantic_model=Example_List)
	# print(response)
	# print("OLLAMA\n====================================")
	# response = model.query_ollama("Name ten mammals", pydantic_model=Example_List)
	# print(response)
	# prompt = Prompt("sing a song about john henry")
	# parsed_prompt = Prompt("Name five birds.")
	# model = Model('gpt')
	# parser = Parser(Example_List)
	# # chain = Chain(prompt, model)
	# parsed_chain = Chain(parsed_prompt, model, parser)
	# # print(chain.run())
	# response = parsed_chain.run()
	# print(response.content.__dict__)

# testing chat functionality
# first, message passing
messages = Chain.create_messages(system_prompt="You're a goddamn pirate.")

for m in ['ollama', 'gpt', 'claude']:
	prompt = Prompt("Sing a song in three lines.")
	model = Model(m)
	chain = Chain(prompt, model)
	response = chain.run(messages = messages)
	print(response.messages)

