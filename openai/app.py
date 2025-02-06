import os
import re
import io
import uuid
import time
import json
import discord
import asyncio
import logging
import pandas as pd
from openai import OpenAI
from discord import Attachment
from duckduckgo_search import AsyncDDGS
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
from discord.ext import commands
import aiohttp
import aioboto3
from PIL import Image
from concurrent_log_handler import ConcurrentRotatingFileHandler
# Uncomment the following two lines to load environment variables from a .env file
# from dotenv import load_dotenv
# load_dotenv()

#-------------------------------------------------#
# Parameters/configuration
#-------------------------------------------------#
VERSION = "2"
NAME_OF_BOT = "turty"
S3_BUCKET = "example"
S3_PREFIX = "gotc/"
BASE_URL = f"https://{S3_BUCKET}.s3.amazonaws.com/{S3_PREFIX}"
SYS_PROMPT = F"""Your name is {NAME_OF_BOT}, you are funny, light-hearted, helpful and you keep your answers simple and clear. You also daydrink a lot and answer questions about the mobile game 'Game of Thrones: Conquest'. Keep your responses short, informative, and please dont try too hard. Provide links to sources in given context if anything."""

#-------------------------------------------------#
# Concurrent Log Handler
#-------------------------------------------------#
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# Set up the rotating file handler with a 9 MB maximum file size
log_file_path = os.path.abspath(f'logs/{NAME_OF_BOT}.log')
rotating_handler = ConcurrentRotatingFileHandler(log_file_path, "a", 9*1024*1024, 10)
rotating_handler.setLevel(logging.INFO)
rotating_handler.setFormatter(logging.Formatter(LOG_FORMAT))

# Set up the root logger and add the rotating file handler
logging.basicConfig(level=logging.INFO,
					format=LOG_FORMAT,
					handlers=[rotating_handler])

# Create a logger for the module and set it to propagate to the root logger
logger = logging.getLogger(f'{NAME_OF_BOT}')

#-------------------------------------------------#
# Discord bot setup
#-------------------------------------------------#
intents = discord.Intents.default()
intents.message_content = True
client = commands.Bot(command_prefix='!', intents=intents)
ai_client = OpenAI()

#-------------------------------------------------#
# Create a command tree
#-------------------------------------------------#
tree = client.tree

async def upload_to_s3(file_name, image, bucket_name='example'):
	"""
	Uploads the given image file to S3 bucket with the given filename and returns the image's new URL.
	URL is public through ACL. All items expire after 1 day.
	:param file_name: The name of the file to upload
	:param image: The image file to upload
	:param bucket_name: The name of the S3 bucket
	"""
	# Convert the Pillow image to a BytesIO object
	img_byte_arr = io.BytesIO()
	image.save(img_byte_arr, format='PNG')
	img_byte_arr = img_byte_arr.getvalue()

	# Initialize aioboto3 client
	session = aioboto3.Session()
	async with session.client('s3', region_name='us-west-1') as s3_client:
		# Upload the image with public-read ACL
		try:
			await s3_client.put_object(
				Bucket=bucket_name,
				Key=file_name,
				Body=img_byte_arr,
				ContentType='image/png',
				ACL='public-read'
			)
			logger.info(f"Image uploaded successfully to {bucket_name}/{file_name}")
			return get_image_url(file_name)
		except Exception as e:
			logger.error(f"Error occurred: {e}")
			return None

async def upload_file_to_s3(file_name, file_bytes, bucket_name='example', region_name='us-west-1'):
	"""
	Uploads the given file bytes to S3 bucket with the given filename and returns a presigned URL that expires in 1 hour.
	:param file_name: The name of the file to upload
	:param file_bytes: The file bytes to upload
	:param bucket_name: The name of the S3 bucket
	:param region_name: The AWS region name
	"""
	# Initialize aioboto3 client
	session = aioboto3.Session()
	async with session.client('s3', region_name=region_name) as s3_client:
		# Upload the file without public-read ACL
		try:
			await s3_client.put_object(
				Bucket=bucket_name,
				Key=file_name,
				Body=file_bytes,
				ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
			)
			logger.info(f"File uploaded successfully to {bucket_name}/{file_name}")

			# Generate a presigned URL that expires in 1 hour (3600 seconds)
			presigned_url = await s3_client.generate_presigned_url(
				'get_object',
				Params={'Bucket': bucket_name, 'Key': file_name},
				ExpiresIn=3600  # 1 hour in seconds
			)
			return presigned_url
		except Exception as e:
			logger.error(f"Error occurred: {e}")
			return None

def get_image_url(file_name, bucket_name='example'):
	"""
 	Get the public URL of the image file in the S3 bucket.
	:param file_name: The name of the image file
	:param bucket_name: The name of the S3 bucket
	"""
	return f"https://{bucket_name}.s3.amazonaws.com/{file_name}?v=1"

async def all_in_one_search(message):
	"""
	Function to perform both DuckDuckGo and Google searches and return the results.
	:param message: The search query
	:return: The search results from DuckDuckGo and Google
	"""
	def google_search(search_term, api_key, cse_id, **kwargs):
		try:
			service = build("customsearch", "v1", developerKey=os.getenv('GOOGLE_API_KEY'))
			res = service.cse().list(q=search_term, cx=os.getenv('GOOGLE_CSE_ID'), **kwargs).execute()
			search_results = []
			for result in res.get('items', []):
				search_results.append({
					'title': result.get('title'),
					'link': result.get('link'),
					'snippet': result.get('snippet'),
					'htmlsnippet': result.get('htmlSnippet')
				})
			return search_results
		except Exception as e:
			logger.error(f"Google search failed: {e}")
			return None

	# Function to perform DuckDuckGo search
	async def duckduckgo_search(message):
		try:
			web_results = await AsyncDDGS().atext(
				f'{message}',
				region='wt-wt',
				safesearch='off',
				timelimit='y',
				max_results=4
			)
			return web_results
		except Exception as e:
			if "rate limit" in str(e) or "HTTP 202" in str(e):
				logger.error("Rate-limited or received HTTP 202 from DuckDuckGo; returning an empty string.")
			else:
				logger.error(f"DuckDuckGo search failed: {e}")
			return None

	# Run both searches
	duck_results = await duckduckgo_search(message)
	google_results = google_search(message, os.getenv('GOOGLE_API_KEY'), os.getenv('GOOGLE_CSE_ID'), num=4)

	# Return the results based on availability
	if duck_results:
		return {'duckduckgo': duck_results}
	elif google_results:
		return {'google': google_results}
	else:
		return ""

async def process_message_with_images(message, list_of_image_urls, system_prompt=SYS_PROMPT):
	"""
	Process the message with the list of image URLs.
	:param message: The message to process
	:param list_of_image_urls: The list of image URLs
	:param system_prompt: The system prompt for the AI model
	:return: The response from the AI model
	"""
	time.sleep(1)
	#-------------------------------------------------#
	# Regenerate message content & reponse based on 
	# rephrased question.
	#-------------------------------------------------#
	system_prompt = """Your name is turty, you are funny, light-hearted, helpful and you keep your answers simple and clear. You also daydrink a lot and answer questions about the mobile game 'Game of Thrones: Conquest'. Keep your responses short, informative,
	and don't try too much."""
	web_results = await all_in_one_search(message)
	message_content = [{"type": "text", "text": f"Original question: {message}.\n\n Additional web results that might or might not help (ignore if not relevant): {web_results}"}]
	
	# Add each image URL to the message content
	for image_url in list_of_image_urls:
		message_content.append({
			"type": "image_url",
			"image_url": {
				"url": f"{image_url}"
			}
		})
	res = ai_client.chat.completions.create(
		model="gpt-4o-mini",
		messages=[
			{
				"role": "system",
				"content": f"{system_prompt}"
			},
			{
				"role": "user",
				"content": message_content
			}
		],
	)
	
	logger.info(f"Response: {res}")
	return res.choices[0].message.content

def prevent_url_embeds(text):
	"""Function to detect and wrap URLs in < and >
	This prevents Discord from embedding the URLs in the message.
	:param text: The text to process
	:return: The text with URLs wrapped in < and >"""
	# Regular expression to match URLs
	url_pattern = r'(https?://\S+)'
	return re.sub(url_pattern, r'<\1>', text)

async def process_message(message, image_urls=[], system_prompt=SYS_PROMPT):
	"""
	Process the message with the list of image URLs.
	:param message: The message to process
	:param image_urls: The list of image URLs
	:param system_prompt: The system prompt for the AI model
	:return: The response from the AI model
	"""
	web_results = await all_in_one_search(f'{message} in Game of Thrones: Conquest mobile game')
	res = ai_client.chat.completions.create(
						model="gpt-4o-mini",
						messages=[
							{"role": "system","content": f"{system_prompt}"},
							{"role": "user","content": f"{message} \n\n Web results: {web_results}"}
						],
					)
	logger.info(f"Response: {res}")
	return res.choices[0].message.content

async def generate_json_call_for_summarize_function(natural_language_request):
	"""
 	Generate JSON body to pass summarize request to the summarize function
	:param natural_language_request: The natural language request
	:return: The JSON body for the summarize function
	"""
	sys_prompt = """Your job is to create the JSON body for an API call to get
		a summary of a Discord conversation x days, y hours ago.

		Example request: Summarize channel conversation since yesterday.
		Example JSON:
		{
			"days": "1",
			"hours": "0",
			"special": ""
		}

		Another example: Summarize channel conversation for past 5 hours, as a 50 word eminem rap
		Example JSON:
		{
			"days": "0",
			"hours": "5",
			"special": "write as 50 word eminem rap"
		}
		"""
	completion = await asyncio.to_thread(
		ai_client.chat.completions.create,
		model="gpt-4o-mini",
		messages=[
			{"role": "system", "content": sys_prompt},
			{"role": "user", "content": natural_language_request},
		],
		response_format={"type": "json_object"},
	)
	return json.loads(completion.choices[0].message.content)

async def decide_what_to_do(raw_message, name_of_bot=NAME_OF_BOT):
	"""
	Generate JSON with what to do next
	:param raw_message: The raw message from the user
	:param image_urls: The list of image URLs
	:return: The JSON body for the next action
	"""
	sys_prompt = f"""Your job is to create the JSON body that tells the bot what to do next.
	Your options include: (1) summarize, (2) gotc, (3) image, (4) help, (5) analyze_user, (6) web_search, (7) humor, (8) about_me, (9) generate_image, (10) miscellaneous, (12) about_chat, (13) calendar, (14) translate, or if nothing else fits then (15) none. Do process foreign languages, the 'todo' key should be in English, the 'language' key should indicate the name of the language to reply in. If they ask you ({name_of_bot}) a question directly it should categorize it as miscellaneous.

	If you get questions about creatures, building, or other game elements that could be considered part of game of thrones conquest game, categorize it as gotc.

	Example A: Summarize channel conversation since yesterday.
	Example JSON A:
	{{
	"todo": "summarize",
	"language": "english"
	}}

	Example B: {name_of_bot} tell me about me/analyze me
	Example JSON B:
	{{
	"todo": "analyze_user",
	"language": "english"
	}}

	Example C: {name_of_bot} que es el pale steel?
	Example JSON C:
	{{
	"todo": "gotc",
	"language": "spanish"
	}}

	Example D: {name_of_bot}, any news about gotc?
	JSON D:
	{{
	"todo": "web_search",
	"language": "english"
	}}

	Example E: yeah not like {name_of_bot}, that dude is wild haha
	JSON E:
	{{
	"todo": "humor",
	"language": "english"
	}}

	Example F: sure, but lets make sure {name_of_bot} is in the loop
	JSON F:
	{{
	"todo": "none",
	"language": "english"
	}}

	Exampple G: {name_of_bot} tell me about me -or- {name_of_bot} analyze me
	JSON G:
	{{
	"todo": "about_me",
	"language": "english"
	}}

	Example H: {name_of_bot} write a love story about caim and iceman
	JSON H:
	{{
	"todo": "miscellaneous",
	"language": "english"
	}}

	Example I: {name_of_bot} what can you do
	JSON I:
	{{
	"todo": "help",
	"language": "english"
	}}

	Example J: whats mereneese honor and is it in regular creatures?
	JSON J:
	{{
	"todo": "miscellaneous",
	"language": "english"
	}}

	Example K: {name_of_bot} make a song about the alliance
	JSON K:
	{{
	"todo" "about_chat",
	"language": "english"
	}}

	Example L: {name_of_bot}, when is the next building event?
	JSON L:
	{{
	"todo": "calendar",
	"language": "english"
	}}

	Example M: {name_of_bot}, show calendar
	JSON M:
	{{
	"todo": "calendar",
	"language": "english"
	}}

	Example N: {name_of_bot}, translate how come erion to french
	JSON N:
	{{
	"todo": "translate",
	"language": "french"
	}}
	"""
	completion = await asyncio.to_thread(
		ai_client.chat.completions.create,
		model="gpt-4o",
		messages=[
			{"role": "system", "content": sys_prompt},
			{"role": "user", "content": raw_message},
		],
		response_format={"type": "json_object"},
	)
	return json.loads(completion.choices[0].message.content)


async def summarize(text_to_summarize, name_of_bot=NAME_OF_BOT):
	"""
	Summarize the given text using the AI model.
	:param text_to_summarize: The text to summarize
	:param name_of_bot: The name of the bot
	:return: The summarized text
	"""
	system_prompt = f"""You are {name_of_bot}, a helpful and kind assistant. You make short summaries using casual language, and if any, you follow the special instructions to the letter. Keep summaries short and informative. Refer to users by name if it helps, use bullet points, bold, and italics where needed. Try to keep under 200 words"""
	res = await asyncio.to_thread(
		ai_client.chat.completions.create,
		model="gpt-4o-mini",
		messages=[
			{"role": "system", "content": system_prompt},
			{"role": "user", "content": text_to_summarize},
		],
	)
	return res.choices[0].message.content

async def what_user_are_they_talking_about(message, name_of_bot=NAME_OF_BOT):
	"""
	Extract the username from the user message.
	:param message: The user message
	:param name_of_bot: The name of the bot
	:return: The JSON with the full username of the user they are talking about
	"""
	system_prompt = f"""Your job is to return a JSON with the full username of the user they are talking about.
	Example: hey {name_of_bot}, analyze user 'john_doe333'
	JSON: {{"user": "john_doe333"}}"""
	res = await asyncio.to_thread(
		ai_client.chat.completions.create,
		model="gpt-4o-mini",
		messages=[
			{"role": "system", "content": system_prompt},
			{"role": "user", "content": message},
		],
		response_format={"type": "json_object"},
	)
	return json.loads(res.choices[0].message.content)

async def make_json_of_image_request(message, image_urls=[], name_of_bot=NAME_OF_BOT):
	"""
	Extract the image request from the user message and create a JSON object with the image request and the size of the image.
	Size options are 1024x1024, 1024x1792, or 1792x1024. If unsure default to 1024x1024.
	:param message: The user message
	:param image_urls: The list of image URLs
	:param name_of_bot: The name of the bot
	:return: The JSON object with the image request and the size of the image
 	"""
	system_prompt = f"""Your task is to extract the image request from the user message and create a JSON object with the image request. and the size of the image.
	Size options are  1024x1024, 1024x1792 or 1792x1024. If unsure default to 1024x1024.
	- 1024x1024: regular/default
	- 1024x1792: tall
	- 1792x1024: wide
 
	Example A: {name_of_bot}, make an image of a droplet of fire drinking coffee
	JSON A: {{
	 	"image_request": "a droplet of fire drinking coffee",
	 	"size": "1024x1024"
	}}
	
	Example B: {name_of_bot} make a tall image of a dragon holding a sword
	JSON B: {{
		"image_request": "a dragon holding a sword",
		"size": "1024x1792"
	}}

	Eample C: {name_of_bot} make a wide image of a castle in the sky
	JSON C: {{
		"image_request": "a castle in the sky",
		"size": "1792x1024"
	}}
	"""
	if image_urls == []:
		res = await asyncio.to_thread(
			ai_client.chat.completions.create,
			model="gpt-4o-mini",
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": message},
			],
			response_format={"type": "json_object"},
		)
		return json.loads(res.choices[0].message.content)
	else:
		message_content = [{"type": "text", "text": f"{message}"}]
		# Add each image URL to the message content
		for image_url in image_urls:
			message_content.append({
				"type": "image_url",
				"image_url": {
					"url": f"{image_url}"
				}
			})
		res = await asyncio.to_thread(
			ai_client.chat.completions.create,
			model="gpt-4o-mini",
			messages=[
				{
					"role": "system",
					"content": f"{system_prompt}"
				},
				{
					"role": "user",
					"content": message_content
				}
			],
			response_format={"type": "json_object"},
		)
		return json.loads(res.choices[0].message.content)
	

async def process_with_web_search(message, image_urls=[], name_of_bot=NAME_OF_BOT):
	"""
	Process the message with the list of image URLs and perform a web search.
	:param message: The message to process
	:param image_urls: The list of image URLs
	:param name_of_bot: The name of the bot
	:return: The response from the AI model
	"""
	system_prompt = f"""Your name is {name_of_bot}, a kind and helpful assitant that day drinks sometimes. Answer the question based on the 
	information provided and your own internal knowledge. Provide links to sources in given context if anything."""
	# web_results = await AsyncDDGS().atext(f'{message}', region='wt-wt', safesearch='off', timelimit='y', max_results=4)
	web_results = await all_in_one_search(message)
	if image_urls == []:
		res = await asyncio.to_thread(
			ai_client.chat.completions.create,
			model="gpt-4o-mini",
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": f"Answer question based on the search results. Question: {message}. \n\n Web results: {web_results}"},
			],
		)
		return res.choices[0].message.content
	else:
		message_content = [{"type": "text", "text": f"Answer question based on the search results. Question: {message}. \n\n Web results: {web_results}"}]
		# Add each image URL to the message content
		for image_url in image_urls:
			message_content.append({
				"type": "image_url",
				"image_url": {
					"url": f"{image_url}"
				}
			})
		res = await asyncio.to_thread(
			ai_client.chat.completions.create,
			model="gpt-4o-mini",
			messages=[
				{
					"role": "system",
					"content": f"{system_prompt}"
				},
				{
					"role": "user",
					"content": message_content
				}
			],
		)
		return res.choices[0].message.content

async def extract_special_categories_json(message, name_of_bot=NAME_OF_BOT):
	"""
	Extract the special categories requested by user to analyze someone & JSON object with the special categories.
	If unsure return an empty string.
	:param message: The user message
	:param name_of_bot: The name of the bot
	:return: The JSON object with the special categories
	"""
	system_prompt = f"""Extract the special categories requested by user to analyze someone & JSON object with the special categories. If unsure return an empty string.
	Example A: {name_of_bot}, analyze user 'john_doe333' in terms of historical figure from the cold war like gorbachev and reagan
	JSON A: {{
		"special_categories": "gorbatchev, reagan, stalin, krushev, thatcher"
	}}
	Example B: {name_of_bot}, analyze user jane_scorsese in personality traits
	JSON B:
	{{
		"special_categories": "optimism, pessimism, introversion, extroversion, neuroticism, agreeableness, conscientiousness, openness"
	}}
	Example C: {name_of_bot} analyze me aksjfdlkajfslk
	JSON C:
	{{
		"special_categories": ""
	}}
	"""
	res = await asyncio.to_thread(
		ai_client.chat.completions.create,
		model="gpt-4o-mini",
		messages=[
			{"role": "system", "content": system_prompt},
			{"role": "user", "content": message},
		],
		response_format={"type": "json_object"},
	)
	return json.loads(res.choices[0].message.content)

async def process_humor(last_couple_of_messages, image_urls=[], name_of_bot=NAME_OF_BOT):
	"""
	Process the message with the list of image URLs and generate a humorous response.
	:param last_couple_of_messages: The last couple of messages
	:param image_urls: The list of image URLs
	:param name_of_bot: The name of the bot
	:return: The response from the AI model
	"""
	# choose 3 at random
	system_prompt = f"""Your name is {name_of_bot}, you are funny, light-hearted, helpful and you keep your answers simple and clear. You also daydrink a lot. Make a funny and witty remarks in response to the conversation provided
	"""
	if image_urls == []:
		res = await asyncio.to_thread(
			ai_client.chat.completions.create,
			model="gpt-4o-mini",
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": f"Last couple of messages {last_couple_of_messages}"},
			],
		)
		return res.choices[0].message.content
	else:
		message_content = [{"type": "text", "text": f"Last couple of messages {last_couple_of_messages}"}]
		# Add each image URL to the message content
		for image_url in image_urls:
			message_content.append({
				"type": "image_url",
				"image_url": {
					"url": f"{image_url}"
				}
			})
		res = await asyncio.to_thread(
			ai_client.chat.completions.create,
			model="gpt-4o-mini",
			messages=[
				{
					"role": "system",
					"content": f"{system_prompt}"
				},
				{
					"role": "user",
					"content": message_content
				}
			],
		)
		return res.choices[0].message.content

async def process_analyze_user(message, special_categories="", image_urls=[], name_of_bot=NAME_OF_BOT):
	"""
	Process the message with the list of image URLs and analyze the user based on the special categories.
	:param message: The message to process
	:param special_categories: The special categories to analyze the user
	:param image_urls: The list of image URLs
	:param name_of_bot: The name of the bot
	:return: The response from the AI model
	"""
	system_prompt = f"""Your name is {name_of_bot}, a kind and helpful assitant that day drinks sometimes. Based on all the user's comments so far, provide a summary of the user's personality in the form a rating scale from 1 to 10 for several different categories. Example:
Analysis:
* Likely to be a spy: 10/10
* Likely to be a bot: 0/10
Reasoning:
User commented 'i think im a spy' on 4/22, and their messages dont seem likely to be written by a bot as evidenced by their spelling mistakes."""
	if image_urls == []:
		res = await asyncio.to_thread(
			ai_client.chat.completions.create,
			model="gpt-4o-mini",
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": f"Ccategories to rate user in based on their comments: {special_categories} \n\n User comment history: {message} \n\n Answer:"},
			],
		)
		return res.choices[0].message.content
	else:
		message_content = [{"type": "text", "text": f"{message}"}]
		# Add each image URL to the message content
		for image_url in image_urls:
			message_content.append({
				"type": "image_url",
				"image_url": {
					"url": f"{image_url}"
				}
			})
		res = await asyncio.to_thread(
			ai_client.chat.completions.create,
			model="gpt-4o-mini",
			messages=[
				{
					"role": "system",
					"content": f"{system_prompt}"
				},
				{
					"role": "user",
					"content": message_content
				}
			],
		)
		return res.choices[0].message.content

async def miscellaneous_reply(message, image_urls=[], name_of_bot=NAME_OF_BOT):
	"""
	Process the message with the list of image URLs and generate a miscellaneous response.
	:param message: The message to process
	:param image_urls: The list of image URLs
	:param name_of_bot: The name of the bot
	:return: The response from the AI model
	"""
	system_prompt = f"""Your name is {name_of_bot}, a kind and helpful assitant that day drinks sometimes. Based on the user's message, provide a response that is both informative and engaging. Use web results included if NEEDED ONLY. else ignore them. Keep it short and to the point."""
	web_results = await all_in_one_search(f'{message} in Game of Thrones: Conquest mobile game')
	if image_urls == []:
		res = await asyncio.to_thread(
			ai_client.chat.completions.create,
			model="gpt-4o-mini",
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": f"{message}\n\n Web results: {web_results} \n\n Response:"},
			],
		)
		return res.choices[0].message.content
	else:
		message_content = [{"type": "text", "text": f"{message}\n\n Web results: {web_results} \n\n Response:"}]
		# Add each image URL to the message content
		for image_url in image_urls:
			message_content.append({
				"type": "image_url",
				"image_url": {
					"url": f"{image_url}"
				}
			})
		res = await asyncio.to_thread(
			ai_client.chat.completions.create,
			model="gpt-4o-mini",
			messages=[
				{
					"role": "system",
					"content": f"{system_prompt}"
				},
				{
					"role": "user",
					"content": message_content
				}
			],
		)
		return res.choices[0].message.content


async def translation_reply(content, image_urls, user_display_name, language, name_of_bot=NAME_OF_BOT):
	"""
	Translate the given text and image descriptions to the requested language.
	:param content: The content to translate
	:param image_urls: The list of image URLs
	:param user_display_name: The display name of the user
	:param language: The language to translate to
	:param name_of_bot: The name of the bot
	:return: The translated response
	"""
	system_prompt = f"""Your name is {name_of_bot}, a kind and helpful assitant that day drinks sometimes. Your role is to translate the given text. If an image is present translate and describe as well. Return only the translated message prefaced with the name of the sender in brackets, like: [DataProphet] Comme ça, vous pouvez voir comment cela fonctionne."""
	if image_urls == []:
		res = await asyncio.to_thread(
			ai_client.chat.completions.create,
			model="gpt-4o-mini",
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": f"User name: {user_display_name}.\n\n Content to translate: {content} \n\n Language: {language}\n\n Response:"},
			],
		)
		return res.choices[0].message.content
	else:
		message_content = [{"type": "text", "text": f"User name: {user_display_name}.\n\n Content to translate: {content}\n\n Language: {language}\n\n Response:"}]
		# Add each image URL to the message content
		for image_url in image_urls:
			message_content.append({
				"type": "image_url",
				"image_url": {
					"url": f"{image_url}"
				}
			})
		res = await asyncio.to_thread(
			ai_client.chat.completions.create,
			model="gpt-4o-mini",
			messages=[
				{
					"role": "system",
					"content": f"{system_prompt}"
				},
				{
					"role": "user",
					"content": message_content
				}
			],
		)
		return res.choices[0].message.content

async def context_answer(request, message_history, name_of_bot=NAME_OF_BOT):
	"""
	Generate a response based on the request and the provided chat context.
	:param request: The request from the user
	:param message_history: The chat history
	:param name_of_bot: The name of the bot
	:return: The response from the AI model
	"""
	system_prompt = f"""Your name is {name_of_bot}, a kind and helpful assitant that day drinks sometimes. Based on the request and the provided chat context, provide a fun and short answer."""
	res = await asyncio.to_thread(
		ai_client.chat.completions.create,
		model="gpt-4o-mini",
		messages=[
			{"role": "system", "content": system_prompt},
			{"role": "user", "content": f"Request: {request}. \n\n Chat history: {message_history} \n\n Response:"},
		],
	)
	return res.choices[0].message.content

async def upload_to_s3(file_name, image, bucket_name='example'):
	"""
	Uploads the given image file to S3 bucket with the given filename and returns the image's new URL.
	URL is public through ACL.
	:param file_name: The name of the file
	:param image: The image to upload
	:param bucket_name: The name of the S3 bucket
	:return: The image's new URL
	"""
	# Convert the Pillow image to a BytesIO object
	img_byte_arr = io.BytesIO()
	image.save(img_byte_arr, format='PNG')
	img_byte_arr = img_byte_arr.getvalue()

	# Initialize aioboto3 client
	session = aioboto3.Session()
	async with session.client('s3', region_name='us-west-1') as s3_client:
		# Upload the image with public-read ACL
		try:
			await s3_client.put_object(
				Bucket=bucket_name,
				Key=file_name,
				Body=img_byte_arr,
				ContentType='image/png',
				ACL='public-read'
			)
			logger.info(f"Image uploaded successfully to {bucket_name}/{file_name}")
			return get_image_url(file_name)
		except Exception as e:
			logger.error(f"Error occurred: {e}")
			return None

async def generate_image(message, size="1024x1024"):
	""" 
	Generate an image based on the given message and size.
	:param message: The message to generate the image from
	:param size: The size of the image to generate
	:return: The URL of the generated image
	"""
	# Generate the image using the AI client in a separate thread
	def generate_image_sync():
		return ai_client.images.generate(
			model="dall-e-3",
			prompt=message,
			size=size,
			quality="standard",
			n=1,
		)
	response = await asyncio.to_thread(generate_image_sync)
	image_url = response.data[0].url

	# Download the image from the URL
	async with aiohttp.ClientSession() as session:
		async with session.get(image_url) as resp:
			if resp.status == 200:
				image_data = await resp.read()
				# Open the image data using Pillow
				image = Image.open(io.BytesIO(image_data))

				# Generate a unique filename
				file_name = f"{uuid.uuid4()}.png"

				# Upload the image to S3
				s3_url = await upload_to_s3(file_name, image)
				return s3_url
			else:
				logger.error(f"Failed to download image, status code: {resp.status}")
				return None

def split_message(message, max_length=2000):
	"""
	Split the message into parts of the given maximum length.
	:param message: The message to split
	:param max_length: The maximum length of each part
	:return: The list of message parts
	"""
	return [message[i:i + max_length] for i in range(0, len(message), max_length)]

async def get_json_preferred_language(message, name_of_bot=NAME_OF_BOT):
	"""
	Extract the preferred language from the user message and create a JSON object with the preferred language.
	:param message: The user message
	:param name_of_bot: The name of the bot
	:return: The JSON object with the preferred language
	"""
	sys_prompt = f"""Your task is to extract the preferred language from the user message and create a JSON object with the preferred language.
	Example: {name_of_bot}, tell me about me
	JSON: {{"language": "english"}}"""
	res = await asyncio.to_thread(
		ai_client.chat.completions.create,
		model="gpt-4o-mini",
		messages=[
			{"role": "system", "content": sys_prompt},
			{"role": "user", "content": message},
		],
		response_format={"type": "json_object"},
	)
	return json.loads(res.choices[0].message.content)

#-------------------------------------------------#
# Slash commands
#-------------------------------------------------#
@tree.command(name="summarize", description="Summarizes the recent conversation.")
async def summarize_command(interaction: discord.Interaction, text: str):
	""" 
	Summarize the recent conversation in the channel.
	:param interaction: The interaction object
	:param text: The text to process
	"""
	logger.info(f"/summarize command invoked by {interaction.user.name}")
	await interaction.response.defer()
	content_lower = text.lower()
	channel = interaction.channel
	language = 'English'  # Set default language or extract from user input if necessary

	try:
		json_call_for_summarize_function = await generate_json_call_for_summarize_function(content_lower)
	except Exception as e:
		logger.error(f"Error generating JSON call: {e}. Trying again")
		try:
			json_call_for_summarize_function = await generate_json_call_for_summarize_function(content_lower)
		except Exception as e:
			logger.error(f"Error generating JSON call: {e}. Giving up")
			await interaction.followup.send(f"Error: {e}. Please try again.")
			return

	days = int(json_call_for_summarize_function.get("days", 0))
	hours = int(json_call_for_summarize_function.get("hours", 0))
	special = json_call_for_summarize_function.get("special", "")
	after_time = datetime.now(timezone.utc) - timedelta(days=days, hours=hours)

	if special != "":
		conversation_history_whole = f"Summarize conversation, keep it Extremly Short. FOLLOW SPECIAL INSTRUCTIONS AT ALL COSTS: {special}\nPreferred language: {language}\n"
	else:
		conversation_history_whole = f"Summarize conversation, please keep it extremely short.\nPreferred language: {language}\n"

	# Fetch messages from the channel since 'after_time'
	async for msg in channel.history(after=after_time, oldest_first=True):
		conversation_history_whole += f"[{msg.created_at}] {msg.author}: {msg.content}\n"

	# Process the entire conversation history with the summary function
	summarized_text = await summarize(conversation_history_whole)

	# Send message in parts if necessary
	for part in split_message(summarized_text):
		await interaction.followup.send(part)

#-------------------------------------------------#
# Slash commands
#-------------------------------------------------#	
@tree.command(name="web_search", description="Searches the web for the provided query.")
async def web_search(interaction: discord.Interaction, text: str):
	"""
	Search the web for the provided query.
	:param interaction: The interaction object
	:param text: The text to process
	"""
	logger.info(f"/web_search command invoked by {interaction.user.name} with text: {text}")
	content_lower = text.lower() #+ "in Game of Thrones: Conquest mobile game"
	image_urls = []  # Assuming no images are provided in the slash command
	logger.info(f"Calling web search function for '{content_lower}'. Images present = {bool(image_urls)}")
	try:
		# Defer the response as processing might take longer than 3 seconds
		await interaction.response.defer()
		reply = await process_with_web_search(content_lower, image_urls)
		# Send message in parts if necessary
		for idx, part in enumerate(split_message(reply)):
			part_no_embeds = prevent_url_embeds(part)
			await interaction.followup.send(part_no_embeds)
	except Exception as e:
		logger.error(f"Error processing web search: {e}")
		await interaction.followup.send(f"Error: {e}. Please try again.")

@tree.command(name="translate", description="Translate.")
async def translate(interaction: discord.Interaction, text: str, language: str = 'english'):
	""" 
	Translate the given text to the specified language.
	:param interaction: The interaction object
	:param text: The text to translate
	:param language: The language to translate to
	"""
	logger.info(f"/translate command invoked by {interaction.user.name} with text: {text} and language: {language}")
	content_lower = text.lower()
	image_urls = []  # Assuming no images are provided in the slash command
	logger.info(f"Calling translate for '{content_lower}' to '{language}'. Images present = {bool(image_urls)}")
	try:
		# Defer the response as processing might take longer than 3 seconds
		await interaction.response.defer()

		# Pass the user's displayed name and the target language to the translation_reply function
		reply = await translation_reply(content_lower, image_urls, interaction.user.display_name, language)
		
		# Send message in parts if necessary
		for idx, part in enumerate(split_message(reply)):
			await interaction.followup.send(part)
	except Exception as e:
		logger.error(f"Error processing translation: {e}")
		await interaction.followup.send(f"Error: {e}. Please try again.")

@tree.command(name='about_me', description='Rates you 1-10 on several different categories based on what you ask for')
async def about_me(interaction: discord.Interaction, text: str):
	""" 
	Analyzes the user based on their recent comments.
	:param interaction: The interaction object
	:param text: The text to process
	"""
	logger.info(f"/about_me command invoked by {interaction.user.name} with text: {text}")
	content_lower = text.lower()
	image_urls = []
	logger.info(f"Calling about me function for '{content_lower}'. Images present = {bool(image_urls)}")
	try:
		# Defer the response as processing might take time
		await interaction.response.defer()

		# Collect all comments from this user in the channel
		all_comments = []
		try:
			async for msg in interaction.channel.history(limit=2000):
				if msg.author == interaction.user:
					all_comments.append(msg.content)
			logger.info(f"Collected comments: {all_comments}")
		except Exception as e:
			logger.error(f"Error collecting comments: {e}")
	
		if all_comments == []:
			await interaction.followup.send("Sorry, I couldn't find any comments from you to analyze. Please try again.")
			return

		try:
			special_categories_json = await extract_special_categories_json(content_lower)
			special_categories = special_categories_json.get("special_categories", "")
		except Exception as e:
			logger.error(f"Error extracting special categories: {e}")
			special_categories = ""

		if not special_categories:
			special_categories = (
				"* Likely to be a spy, Likely to be a bot"
			)

		language_json = await get_json_preferred_language(content_lower)
		language = language_json.get("language", "english")
		logger.info(f"Analyzing user {interaction.user.name} based on their recent comments. Preferred language: {language}. Comments: {' '.join(all_comments)}")
		reply = await process_analyze_user(
			f"Analyze user {interaction.user.name} based on their recent comments. Preferred language: {language}, comments: {' '.join(all_comments)}",
			special_categories
		)

		# Send message in parts if necessary
		for part in split_message(reply):
			await interaction.followup.send(part)
	except Exception as e:
		logger.error(f"Error analyzing user {interaction.user}: {e}")
		await interaction.followup.send(f"Error: {e}. Please try again.")

@tree.command(name="about_chat", description="Provides information about the chat based on recent messages.")
async def about_chat_command(interaction: discord.Interaction, text: str):
	""" 
	Provides information about the chat based on recent messages.
	:param interaction: The interaction object
	:param text: The text to process
	"""
	logger.info(f"/about_chat command invoked by {interaction.user.name}")
	await interaction.response.defer()  # Defer the response as the processing may take time

	content_lower = text.lower()
	channel = interaction.channel

	# Fetch the last 2000 messages from the channel
	conversation_history_whole = ""
	async for msg in channel.history(limit=2000):
		conversation_history_whole += f"[{msg.created_at}] {msg.author}: {msg.content}\n"
	logger.info(f"Conversation history: {conversation_history_whole}")

	# Generate a reply using the context_answer function
	reply = await context_answer(content_lower, conversation_history_whole)

	# Send the reply in parts if necessary
	for part in split_message(reply):
		# Prevent URL embedding in each part before sending
		part_no_embeds = prevent_url_embeds(part)
		await interaction.followup.send(part_no_embeds)

@tree.command(name='analyze_user', description='Analyzes a specified user based on their comments.')
async def analyze_user(interaction: discord.Interaction, text: str):
	""" 
	Analyzes the specified user based on their recent comments.
	:param interaction: The interaction object
	:param text: The text to process
	"""
	logger.info(f"/analyze_user command invoked by {interaction.user.name} with text: {text}")
	image_urls = []
	try:
		# --------------------------------#
		# Correct mentions in the input text
		# --------------------------------#
		modified_content = text
		# Find all user mentions in the text (e.g., <@1234567890> or <@!1234567890>)
		user_id_mentions = re.findall(r'<@!?(\d+)>', modified_content)
		for user_id in user_id_mentions:
			user = await interaction.guild.fetch_member(int(user_id))
			
			if user:
				user_display_name = user.name
				# Replace the mention with the display name
				modified_content = re.sub(f'<@!?{user_id}>', user_display_name, modified_content)
		# Use the modified content for further processing
		content_lower = modified_content.lower()
		logger.info(f"Content with corrected mentions: {content_lower}")
	except Exception as e:
		logger.error(f"Error processing mentions: {e}")
		await interaction.response.send_message(f"I didn't understand the @ mention. Please try again: {e}")
		return
	try:
		# Defer the response as processing may take time
		await interaction.response.defer()

		# Get the preferred language from the content
		language_json = await get_json_preferred_language(content_lower)
		language = language_json.get("language", "english")

		# Determine which user to analyze
		what_user = await what_user_are_they_talking_about(content_lower)
		what_user_username = what_user.get("user", "none")

		if what_user_username == "none":
			await interaction.followup.send("Sorry, I didn't catch the user you want me to analyze. Please try again.")
			return
		else:
			await interaction.followup.send(f"Analyzing user: {what_user_username}, in {language}")

		# Collect all comments from the specified user in the channel
		all_comments = []
		try:
			async for msg in interaction.channel.history(limit=2000):
				if msg.author.name == what_user_username:
					all_comments.append(msg.content)
			logger.info(f"Collected comments: {all_comments}")
		except Exception as e:
			logger.error(f"Error collecting comments: {e}")
			# send message that there is nothing to analyze
			await interaction.followup.send("Sorry, I couldn't find any comments from the user to analyze. Please try again.")
			return

		try:
			special_categories_json = await extract_special_categories_json(content_lower)
			special_categories = special_categories_json.get("special_categories", "")
			logger.info(f"Special categories: {special_categories} for user: {what_user_username} - analyzing user")
		except Exception as e:
			logger.error(f"Error extracting special categories: {e}")
			special_categories = ""

		if not special_categories:
			special_categories = (
				"Likely to be a spy, Likely to be a bot"
			)

		# Process the analysis
		logger.info(f"Analyzing user {what_user_username} based on their recent comments. Preferred language: {language}. Comments: {' '.join(all_comments)}")
		reply = await process_analyze_user(
			f"Analyze user {what_user_username} based on their recent comments. Preferred language: {language}. Comments: {' '.join(all_comments)}",
			special_categories
		)
		logger.info(f"Reply: {reply}")
		# Send the reply in parts if necessary
		for part in split_message(reply):
			await interaction.followup.send(part)

	except Exception as e:
		logger.error(f"Error analyzing user {what_user_username}: {e}")
		await interaction.followup.send(f"Error: {e}. Please try again.")

@tree.command(
	name='gotc',
	description='Answers questions about the mobile game "Game of Thrones: Conquest".'
)
async def gotc(
	interaction: discord.Interaction,
	text: str,
	image: Attachment = None  # Accepts an optional image attachment
):
	""" 
	Answers questions about the mobile game "Game of Thrones: Conquest".
	:param interaction: The interaction object
	:param text: The text to process
	:param image: The image attachment
	"""
	content_lower = text.lower()
	image_urls = []

	# Check if an image attachment was provided
	if image:
		# Check if the attachment is an image
		if any(image.filename.lower().endswith(ext) for ext in ['jpg', 'jpeg', 'png', 'gif', 'bmp']):
			image_urls.append(image.url)
		else:
			await interaction.response.send_message(
				"The attachment must be an image file.",
				ephemeral=True
			)
			return

	logger.info(f"Calling gotc function for '{content_lower}'. Images present = {bool(image_urls)}")

	try:
		# Defer the response as processing might take time
		await interaction.response.defer()

		if image_urls:
			reply = await process_message_with_images(content_lower, image_urls)
		else:
			reply = await process_message(content_lower)

		for part in split_message(reply):
			# Prevent URL embedding in each part before sending
			part_no_embeds = prevent_url_embeds(part)
			await interaction.followup.send(part_no_embeds)
	except Exception as e:
		logger.error(f"Error processing GOTC command: {e}")
		await interaction.followup.send(f"Error: {e}. Please try again.")

@client.event
async def on_ready():
	try:
		await tree.sync()
	except Exception as e:
		logger.error(f"Failed to sync commands: {e}")
	logger.info(f"Logged in as {client.user} (ID: {client.user.id}) for bot: {NAME_OF_BOT}")
	print(f"Logged in as {client.user} (ID: {client.user.id}) for bot: {NAME_OF_BOT}")


async def build_assets_map_s3():
	""" 
	Builds a map of assets from the S3 bucket.
	:return: A dictionary mapping single-image commands to their URLs and a dictionary mapping directory commands to their URLs
	"""
	logger.debug("Starting build_assets_map_s3")
	name_to_link = {}
	dir_to_links = {}

	logger.debug("Creating a new aioboto3 session")
	session = aioboto3.Session()
	async with session.client('s3', region_name='us-east-1') as s3:
		logger.debug(f"Listing objects in bucket '{S3_BUCKET}' with prefix '{S3_PREFIX}'.")
		response = await s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_PREFIX)
		logger.debug(f"Received response from S3: {response}")

		if 'Contents' not in response:
			logger.debug("No 'Contents' key in response, returning empty dictionaries.")
			return name_to_link, dir_to_links

		logger.debug(f"Found {len(response['Contents'])} objects under prefix '{S3_PREFIX}'")
		for obj in response['Contents']:
			key = obj['Key']
			logger.debug(f"Processing object key: {key}")

			relative_path = key[len(S3_PREFIX):]
			parts = relative_path.split('/')
			logger.debug(f"relative_path: {relative_path}, parts: {parts}")

			if len(parts) == 1:
				# Single image: "command.png"
				image_name = parts[0][:-4].lower()
				image_link = BASE_URL + parts[0]
				# add ending ?v=1 to the image link to prevent caching
				image_link += f"?v={VERSION}"
				name_to_link[image_name] = image_link
				logger.debug(f"Mapped single-image command '{image_name}' to '{image_link}'")
			else:
				# Directory with multiple images: "command/image.png"
				dir_name = parts[0].lower()
				image_link = BASE_URL + '/'.join(parts)
				if dir_name not in dir_to_links:
					dir_to_links[dir_name] = []
					logger.debug(f"Created new directory entry for '{dir_name}'")
				dir_to_links[dir_name].append(image_link)
				logger.debug(f"Added '{image_link}' to directory '{dir_name}'")

	logger.debug(f"Finished build_assets_map_s3: {len(name_to_link)} single-image commands, {len(dir_to_links)} directory commands found.")
	return name_to_link, dir_to_links

#-------------------------------------------------#
# Message event
#-------------------------------------------------#
@client.event
async def on_message(message):
	logger.debug("on_message event triggered.")
	if message.author == client.user:
		logger.debug("Message author is the bot itself; ignoring.")
		return
	# gather image_urls
	image_urls = []
	for attachment in message.attachments:
		if attachment.url.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
			image_urls.append(attachment.url)

	content_lower = message.content.lower().strip()
	displayed_name_of_user_sending_message = message.author.display_name
	logger.debug(f"Received message: {message.content} (lower: {content_lower}) from {message.author}")
	cal_link = "https://example.s3.us-east-1.amazonaws.com/gotc/calendar.png"


	if content_lower.startswith('!help'):
		logger.info(f"Calling help function for {content_lower}.")
		help_message = """**Gear**  
		- `!utility` — Utility Gear Chart  
		- `!marchsize` — March Size Gear Chart  
		- `!marchspeed` — March Speed Gear Chart  
		- `!sopgear` — Versus Seat of Power Gear Chart  
		- `!dragon` — Dragon Wearables Chart

		**Troop Type Charts**  
		- `!trooptier` — Troop Tier Chart  
		- `!siege`  
		- `!infantry` — Infantry Recommendations  
		- `!inf-troop` — Infantry vs. Other Troop Types  
		- `!inf-dragon` — Infantry Dragon Pieces  
		- `!cavalry` — Cavalry Recommendations  
		- `!cav-troop` — Cavalry vs. Other Troop Types  
		- `!cav-dragon` — Cavalry Dragon Pieces  
		- `!range` — Range Recommendations  
		- `!range-troop` — Range vs. Other Troop Types  
		- `!range-dragon` — Range Dragon Pieces

		**Gear Seasons & Materials**  
		- `!season#` (1–10) — Gear sets & materials for that season  
		- `!sopgear` — SOP Gear  
		- `!advmat` — Advanced Materials  
		- `!crafting-materials` — Material needs per quality  
		- `!template-cost` — Gold cost of templates

		**Buildings & SOPs**  
		- `!stat4`  
		- `!trainingsop`  
		- `!steel`  
		- `!craftingsop`  
		- `!trainingyard`  
		- `!wall`  
		- `!watchtower`  
		- `!barracks`  
		- `!castle`  
		- `!archery`

		**Creatures & Events**  
		- `!venison`  
		- `!elk`  
		- `!creatures`  
		- `!hotspots`  
		- `!nodes`  
		- `!mergeunlock`  
		- `!lore`

		**Armory**  
		- `!armory-cavalry`  
		- `!armory-infantry`  
		- `!armory-range`

		**Enhancements**
		- `!enhancements-nameofbuilding
	"""
		await message.channel.send(help_message)

	if (content_lower.strip() =='!hero' or content_lower.strip() == '!heroes'):
			logger.info(f"Calling heros function for {content_lower}.")
			help_message = """
		Welcome to the Heroes Menu!
		This menu will list the commands and what each chart has for all of the Hero recommendations and other hero charts.

		**Informational Charts**
		- `!oaths` - the number of Oaths that are needed for each level and rarity, including the gold cost for each.
		- `!relics` - the amount of relics that are required for each level upgrade per rarity and the amount of relics needed to max each rarity type. Excess relics get turned into Silver Stags.

		**Recommendation Charts**
		- `!hero-cavalry` - Cavalry based Hero recommendations.
		- `!hero-infantry` - Infantry based Hero recommendations.
		- `!hero-range` - Range based Hero recommendations.
		- `!hero-siege` - Siege based Hero recommendations.
		- `!hero-dragon` - Dragon based Hero Recommendations.
		- `!hero-marchsize` - Council recommendations for various March Size situations.
		- `!hero-rein` - Council recommendations for Seat of Power Reinforcements.
		- `!hero-rally` - Hero Council recommendations for Rally Capacity.
		- `!hero-farming` - Council recommendations for Farming speed.
		- `!hero-utility` - Recommendations for various Utility councils.

		**Hero Traits**
		- `xp-common`
		- `xp-rare`
		- `xp-heroic`
		- `xp-mythic`
		- `traits-1`
		- `traits-2`
		- `targ-traits`
		- `stark-traits`
		- `lannister-traits`
		- `other-traits`
		- `mastersofcoins`
		- `mastersofships`
		- `masterofwar`
		- `mastersoflaw`
		"""
			await message.channel.send(help_message)

	if content_lower.startswith('!calendar') or content_lower.startswith('!calender'):
		logger.info('Sending calendar')
		await message.channel.send('Here it is')
		await message.channel.send(cal_link)

	# Check for calling the bot by name
	bot_called_by_name = f'{NAME_OF_BOT} ' in content_lower or f'{NAME_OF_BOT} ' in content_lower or f'{NAME_OF_BOT},'
	# Check if the bot was mentioned with @
	bot_mentioned_by_mention = client.user in message.mentions

	# Check if the message is a reply to the bot
	bot_replied_to = False
	referenced_message_content = ""
	if message.reference and message.reference.resolved:
		if message.reference.resolved.author == client.user:
			bot_replied_to = True
			referenced_message_content = message.reference.resolved.content

	# If the bot was called by name, mentioned, or replied to
	if bot_called_by_name or bot_replied_to or bot_mentioned_by_mention:
		#--------------------------------#
		# Conversation history
		#--------------------------------#
		# Gather recent conversation history for context in chronological order
		messages_list = []
		async for msg in message.channel.history(limit=10, oldest_first=False):
			# Format time as 12-hour clock with AM/PM
			timestamp = msg.created_at.strftime("%I:%M %p")
			author = msg.author.display_name
			# Remove newlines from the message content (optional)
			content = msg.content.replace('\n', ' ')
			messages_list.append(f"({timestamp}) {author}: {content}")
		# reverse the list
		messages_list.reverse()
		# Join all messages with newlines
		conversation_history_whole = "\n".join(messages_list)

		# Include the referenced bot message content if this is a reply
		reply_to_note = ""
		if bot_replied_to and referenced_message_content:
			reply_to_note = f"\n This is what you replied to the user before: '{referenced_message_content}'\n"
		if reply_to_note == "":
			content_plus_conversation = f"{content_lower}\n Conversation history: {conversation_history_whole}"
		else:
			content_plus_conversation = f"{content_lower}\n Conversation history: {conversation_history_whole}\n User replied to your previous message: {reply_to_note}"

		#--------------------------------#
		# Correct mentions
		#--------------------------------#
		try:
			# Start with the original message content
			modified_content = content_plus_conversation
			for mention in message.mentions:
				user_display_name = mention.display_name
				# Replace the mention with the display name
				modified_content = modified_content.replace(f"<@{mention.id}>", user_display_name)
			# Do something with the fully modified message
			content_lower = modified_content
			logger.info(f"Content with corrected mentions: {content_lower}")
		except Exception as e:
			logger.error(f"Error processing message: {e}")
			await message.channel.send(f"I didn't understand the @ mention. Please try again: {e}")

		#--------------------------------#
		# alternative correct mentions for username instead of display name
		#--------------------------------#
		try:
			# Start with the original message content
			alt_modified_content = content_plus_conversation
			for mention in message.mentions:
				user_username = mention.name
				# Replace the mention with the display name
				alt_modified_content = alt_modified_content.replace(f"<@{mention.id}>", user_username)
			# Do something with the fully modified message
			alt_content_lower = alt_modified_content
			logger.info(f"Content with corrected mentions: {alt_content_lower}")
		except Exception as e:
			logger.error(f"Error processing message: {e}")
			await message.channel.send(f"I didn't understand the @ mention. Please try again: {e}")

		#--------------------------------#
		# Decide what to do
		#--------------------------------#
		try: 
			json_decide_what_to_do = await decide_what_to_do(content_lower)
		except Exception as e:
			logger.error(f"Error deciding what to do: {e}. Trying again")
			try:
				json_decide_what_to_do = await decide_what_to_do(content_lower)
			except Exception as e:
				logger.error(f"Error deciding what to do: {e}. Giving up")
				await message.channel.send(f"Error: {e}. Please try again.")
		todo = json_decide_what_to_do.get("todo", "none")
		logger.info(f"Decided to do: {todo}")
		language = json_decide_what_to_do.get("language", "english")
		logger.info(f"Decided to reply in: {language}")

		#------------------------------#
		# Summarize
		#------------------------------#
		if todo == "summarize":
			logger.info(f"Calling summarize function for {content_lower}. Images present = {image_urls != []}")
			channel = message.channel
			try:
				json_call_for_summarize_function = await generate_json_call_for_summarize_function(content_lower)
			except Exception as e:
				logger.error(f"Error generating JSON call: {e}. Trying again")
				try:
					json_call_for_summarize_function = await generate_json_call_for_summarize_function(content_lower)
				except Exception as e:
					logger.error(f"Error generating JSON call: {e}. Giving up")
					await message.channel.send(f"Error: {e}. Please try again.")
					return
			days = int(json_call_for_summarize_function.get("days", 0))
			hours = int(json_call_for_summarize_function.get("hours", 0))
			special = json_call_for_summarize_function.get("special", "")
			after_time = datetime.now(timezone.utc) - timedelta(days=days, hours=hours)
			if special != "":
				conversation_history_whole = f"Summarize conversation, keep it extremly short. FOLLOW SPECIAL INSTRUCTIONS AT ALL COSTS: {special}\n Preferred language: {language}\n. Acknowledge user sending that made request: {displayed_name_of_user_sending_message}\n"
			else:
				conversation_history_whole = f"Summarize conversation, please keep it extremley short.\n Preferred language: {language}\n"
			# Fetch messages from the channel since 'after_time'
			async for msg in channel.history(after=after_time):
				conversation_history_whole += f"[{msg.created_at}] {msg.author}: {msg.content}\n"
			# Process the entire conversation history with the summary function
			summarized_text = await summarize(conversation_history_whole)
			for part in split_message(summarized_text):
				# Prevent URL embedding in each part before sending
				part_no_embeds = prevent_url_embeds(part)
				await message.channel.send(part_no_embeds)
		
		#------------------------------#    
		# Gotc
		#------------------------------#
		elif todo == "gotc" or todo == 'web_search' or todo == 'miscellaneous':
			# await message.channel.send("*gotc*")
			logger.info(f"Calling gotc function for {content_lower}. Images present = {image_urls != []}")
			if message.attachments:
				# Initialize a list to store image URLs
				image_urls = []
				# Loop through each attachment
				for attachment in message.attachments:
					# Check if the attachment is an image
					if any(attachment.filename.lower().endswith(image_ext) for image_ext in ['jpg', 'jpeg', 'png', 'gif', 'bmp']):
						# Add image URL to the list
						image_urls.append(attachment.url)
				# Check if there are any image URLs
				if image_urls:
					reply = await process_message_with_images(content_lower, image_urls)
					for part in split_message(reply):
						# Prevent URL embedding in each part before sending
						part_no_embeds = prevent_url_embeds(part)
						await message.channel.send(part_no_embeds)
			else:
				reply = await process_message(f"{content_lower}. Acknowledge user sending that made request: {displayed_name_of_user_sending_message}")
				for part in split_message(reply):
					# Prevent URL embedding in each part before sending
					part_no_embeds = prevent_url_embeds(part)
					await message.channel.send(part_no_embeds)

		#------------------------------#
		# Miscellaneous translation
		#------------------------------#
		elif todo == "translation":
			logger.info(f"Calling translation function for {content_lower}. Images present = {image_urls != []}")
			reply = await translation_reply(f"{content_lower}.", image_urls)
			# Send message in parts if necessary
			for part in split_message(reply):
				await message.channel.send(part)

		#--------------------------------#
		# Context needed
		#--------------------------------#
		elif todo == "about_chat":
			logger.info(f"Calling context needed function for {content_lower}")
			# use the chat history to generate a response
			# get last 200 messages of the conversation
			conversation_history_whole = ""
			async for msg in message.channel.history(limit=2000):
				conversation_history_whole += f"[{msg.created_at}] {msg.author}: {msg.content}\n"
			logger.info(f"Conversation history: {conversation_history_whole}. Acknowledge user sending that made request: {displayed_name_of_user_sending_message}")
			reply = await context_answer(content_lower, conversation_history_whole)
			for part in split_message(reply):
				# Prevent URL embedding in each part before sending
				part_no_embeds = prevent_url_embeds(part)
				await message.channel.send(part_no_embeds)

		#--------------------------------#
		# About me
		#--------------------------------#
		elif todo == "about_me":
			logger.info(f"Calling about me function for {content_lower}. Images present = {image_urls != []}")
			try:
				# All comments from this user so far
				all_comments = []
				async for msg in message.channel.history(limit=2000):
					if msg.author == message.author:
						all_comments.append(msg.content)
				try:
					special_categories_json = await extract_special_categories_json(content_lower)
					special_categories = special_categories_json.get("special_categories", "")
				except Exception as e:
					logger.error(f"Error extracting special categories: {e}")
					special_categories = ""
				if special_categories == "":
					special_categories = """* Likely to be a spy, Likely to be a comedian, Likely to be a politician, Likely to be a gamer, Likely to be a hacker,Likely to be a writer,Likely to be a musician,Likely to be a bot"""
				reply = await process_analyze_user(f"Analyze user {message.author} based on their recent comments. Preferred language: {language}, comments: {' '.join(all_comments)}", special_categories)
				for part in split_message(reply):
					# Prevent URL embedding in each part before sending
					part_no_embeds = prevent_url_embeds(part)
					await message.channel.send(part_no_embeds)
			except Exception as e:
				logger.error(f"Error analyzing user {msg.author}: {e}")
				await message.channel.send(f"Error: {e}. Please try again.")
		
		#--------------------------------#
		# Analyze user
		#--------------------------------#
		elif todo == "analyze_user":
			logger.info(f"Calling analyze user function for {alt_content_lower}. Images present = {image_urls != []}")
			try:
				full_prompt_with_valid_options = f"""{alt_content_lower}"""
				what_user = await what_user_are_they_talking_about(alt_content_lower)
				what_user_username = what_user.get("user", "none")
				if what_user_username == "none":
					await message.channel.send("Sorry I didn't catch the user you want me to analyze, please try again.")
					return
				else:
					await message.channel.send(f"Analyzing user: {what_user_username}, in {language}")
				# All comments from this user so far
				all_comments = []
				async for msg in message.channel.history(limit=2000):
					# get only those messages from the user we are analyzing
					if msg.author.name == what_user_username:
						all_comments.append(msg.content)   
				try:
					special_categories_json = await extract_special_categories_json(alt_content_lower)
					special_categories = special_categories_json.get("special_categories", "")
					logger.info(f"Special categories: {special_categories} for user: {what_user_username}  - analyzing user")
				except Exception as e:
					logger.error(f"Error extracting special categories: {e}")
					special_categories = ""
				if special_categories == "":
					special_categories = """* Likely to be a spy, Likely to be a comedian, Likely to be a politician, Likely to be a gamer, Likely to be a hacker,Likely to be a writer,Likely to be a musician,Likely to be a bot"""
				reply = await process_analyze_user(f"Analyze user {what_user_username} based on their recent comments. Preferred language: {language}. Comments: {' '.join(all_comments)}", special_categories)
				for part in split_message(reply):
					# Prevent URL embedding in each part before sending
					part_no_embeds = prevent_url_embeds(part)
					await message.channel.send(part_no_embeds)
			except Exception as e:
				logger.error(f"Error analyzing user {what_user_username}: {e}")
				await message.channel.send(f"Error: {e}. Please try again.")
		
		#--------------------------------#
		# Humor
		#--------------------------------#
		elif todo == "humor":
			logger.info(f"Calling humor function for {content_lower}. Images present = {image_urls != []}")
			last_couple_of_messages = []
			# gather last 10 messages to reply with humor based on context
			async for msg in message.channel.history(limit=40):
				last_couple_of_messages.append(msg.content)
			# Make sure the list is in order (oldest to newest)
			last_couple_of_messages.reverse()
			logger.info(f'Conversation history: {last_couple_of_messages}. Acknowledge user sending that made request: {displayed_name_of_user_sending_message}')
			reply = await process_humor(last_couple_of_messages)
			# Send message in parts if necessary
			for part in split_message(reply):
				await message.channel.send(part)
		
		#--------------------------------#
		# Generate image
		#--------------------------------#
		elif todo == "generate_image":
			logger.info(f"Calling generate image function for {content_lower}. Images present = {image_urls != []}")
			json_image_request = await make_json_of_image_request(content_lower, image_urls)
			im_request = json_image_request.get("image_request", "none")
			im_size = json_image_request.get("size", "1024x1024")
			await message.channel.send(f"*Generating image for prompt: {im_request}. Size: {im_size}*")
			if im_request == "none":
				await message.channel.send("Sorry I didn't catch the image request, please try again.")
				return
			else:
				try:
					url_image_reply = await generate_image(im_request, im_size)
					await message.channel.send(url_image_reply)
				except Exception as e:
					logger.error(f"Error generating image: {e}")
					await message.channel.send(f"I couldn't generate the image, remember you need to keep it PG-13 and you can't ask for a celebrity/copyrighted stuff.")
		
		#--------------------------------#
		# Help
		#--------------------------------#
		elif todo == "help":
			logger.info(f"Calling help function for {content_lower}. Images present = {image_urls != []}")
			help_message = f"""Hi {displayed_name_of_user_sending_message.strip()}! I am {NAME_OF_BOT}, I summarize conversations, provide general info about the game, search the web, etc. To get started, simply mention me in your message and ask your question. For example, '{NAME_OF_BOT}, summarize the last 3 hours' or '{NAME_OF_BOT} make an image of a cute droplet of fire drinking coffee'. To list citadel commands use !help and !heroes"""
			await message.channel.send(help_message)
		
		#--------------------------------#
		# None
		#--------------------------------#
		elif todo == "none":
			logger.warning(f"Nothing to do for message: {content_lower}.")
			pass
		
		#--------------------------------#
		# Calendar
		#--------------------------------#
		elif todo == "calendar":
			# append cal_link to the image_urls
			image_urls.append(cal_link)
			new_content_lower = f"IF ONLY ASKED TO DISPLAY OR SHOW CALENDAR THEN JUST REPLY 'See calendar below' ELSE ANSER QUESTIONS ABOUT THE CALENDAR FROM THE IMAGE. USER QUERY: {content_lower}\n\ntoday is {pd.Timestamp.now().strftime('%Y-%m-%d')} day if the week is {pd.Timestamp.now().day_name()}"
			reply = await miscellaneous_reply(new_content_lower, image_urls)
			for part in split_message(reply):
				# Prevent URL embedding in each part before sending
				part_no_embeds = prevent_url_embeds(part)
				await message.channel.send(part_no_embeds)
			# also send image of calendar
			await message.channel.send(cal_link)
  
		#--------------------------------#
		# Else
		#--------------------------------#
		else:
			await message.channel.send("Sorry, I didn't understand the request. Please try again.")
			logger.info(f"Calling gotc function for {content_lower}. Images present = {image_urls != []}")
			if message.attachments:
				# Initialize a list to store image URLs
				image_urls = []
				# Loop through each attachment
				for attachment in message.attachments:
					# Check if the attachment is an image
					if any(attachment.filename.lower().endswith(image_ext) for image_ext in ['jpg', 'jpeg', 'png', 'gif', 'bmp']):
						# Add image URL to the list
						image_urls.append(attachment.url)
				# Check if there are any image URLs
				if image_urls:
					reply = await process_message_with_images(f"{content_lower}. Acknowledge user sending that made request: {displayed_name_of_user_sending_message}", image_urls)
					for part in split_message(reply):
						# Prevent URL embedding in each part before sending
						part_no_embeds = prevent_url_embeds(part)
						await message.channel.send(part_no_embeds)
			else:
				reply = await process_message(f"{content_lower}. Acknowledge user sending that made request: {displayed_name_of_user_sending_message}")
				for part in split_message(reply):
					# Prevent URL embedding in each part before sending
					part_no_embeds = prevent_url_embeds(part)
					await message.channel.send(part_no_embeds)

	# Conditions to skip certain commands (optional)
	skip_conditions = [
		content_lower.startswith('!help'),
		content_lower.startswith('!calendar'),
		content_lower.startswith('!image'),
		content_lower.startswith('!calender'),
		content_lower.startswith('!fantasy'),
		content_lower.startswith('!heroes')
	]
	if any(skip_conditions):
		logger.debug("Message matches skip conditions; ignoring command.")
		return

	# Handle mentions
	try:
		logger.debug("Attempting to process mentions in the message.")
		modified_content = message.content
		for mention in message.mentions:
			user_display_name = mention.display_name
			logger.debug(f"Replacing mention <@{mention.id}> with {user_display_name}")
			modified_content = modified_content.replace(f"<@{mention.id}>", user_display_name)
		content_lower = modified_content.lower()
		logger.debug(f"Final content after mention replacements: {content_lower}")
	except Exception as e:
		logger.exception("Error processing mentions:")
		await message.channel.send(f"I didn't understand the @ mention. Please try again: {e}")
		return

	# Parse the command
	logger.debug("Parsing the command from the message.")
	command = content_lower[1:]  # remove '!' prefix
	logger.debug(f"Command extracted (without '!'): {command}")
	parts = command.split()
	logger.debug(f"Command parts: {parts}")

	if not parts:
		logger.debug("No parts found in the command; sending 'Command not recognized.'")
		return

	image_name = parts[0].lower().strip()
	logger.debug(f"Image name extracted: {image_name}")

	# Dynamically load available assets from S3
	logger.debug("Awaiting build_assets_map_s3() to get name_to_link and dir_to_links.")
	name_to_link, dir_to_links = await build_assets_map_s3()
	logger.debug(f"name_to_link keys: {list(name_to_link.keys())}")
	logger.debug(f"dir_to_links keys: {list(dir_to_links.keys())}")

	# Check for single-image command
	if image_name in name_to_link:
		logger.debug(f"Found single image for command '{image_name}': {name_to_link[image_name]}")
		await message.channel.send(name_to_link[image_name])
	# Check for directory-based command
	elif image_name in dir_to_links:
		logger.debug(f"Found directory for command '{image_name}' with {len(dir_to_links[image_name])} images.")
		for link in dir_to_links[image_name]:
			logger.debug(f"Sending link: {link}")
			await message.channel.send(link)
	else:
		logger.debug(f"No single image or directory found for command '{image_name}'.")

def main():
	# Start the bot
	client.run(os.environ['DISCORD_BOT_TOKEN'])

if __name__ == '__main__':
	main()