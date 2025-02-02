import os
import re
import io
import uuid
import base64
import aiohttp
import discord
import logging
import aioboto3
from PIL import Image
from discord.ext import commands
from concurrent_log_handler import ConcurrentRotatingFileHandler

#-------------------------------------------------#
# Concurrent Log Handler
#-------------------------------------------------#
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# Set up the rotating file handler with a 10 MB maximum file size
log_file_path = os.path.abspath('logs/freebot.log')
rotating_handler = ConcurrentRotatingFileHandler(log_file_path, "a", 9*1024*1024, 10)
rotating_handler.setLevel(logging.INFO)
rotating_handler.setFormatter(logging.Formatter(LOG_FORMAT))

# Set up the root logger and add the rotating file handler
logging.basicConfig(level=logging.INFO,
					format=LOG_FORMAT,
					handlers=[rotating_handler])

# Create a logger for the module and set it to propagate to the root logger
logger = logging.getLogger('freebot')

#-------------------------------------------------#
# Discord bot setup
#-------------------------------------------------#
intents = discord.Intents.default()
intents.message_content = True
client = commands.Bot(command_prefix='!', intents=intents)

#-------------------------------------------------#
# Customize character
#-------------------------------------------------#
SYS_PROMPT = "Your name is freebot, you are a friendly and helpful assistant that answers questions in a short and concise manner."
NAME_TO_CALL_BOT = "freebot"
AWS_REGION = "us-east-1"
# Set discord token env variable or declare it here
# os.environ['DISCORD_BOT_TOKEN'] = 'YOUR_DISCORD_BOT_TOKEN'

async def upload_to_s3(file_name, image, bucket_name='example'):
	"""
	Uploads the given image file to S3 bucket with the given filename and returns the image's new URL.
	URL is public through ACL. All items expire after 1 day.
	"""
	# Convert the Pillow image to a BytesIO object
	img_byte_arr = io.BytesIO()
	image.save(img_byte_arr, format='PNG')
	img_byte_arr = img_byte_arr.getvalue()

	# Initialize aioboto3 client
	session = aioboto3.Session()
	async with session.client('s3', region_name=AWS_REGION) as s3_client:
		# Upload the image with public-read ACL
		try:
			await s3_client.put_object(
				Bucket=bucket_name,
				Key=file_name,
				Body=img_byte_arr,
				ContentType='image/png',
				ACL='public-read'
			)
			print(f"Image uploaded successfully to {bucket_name}/{file_name}")
			return get_image_url(file_name)
		except Exception as e:
			print(f"Error occurred: {e}")
			return None

def get_image_url(file_name, bucket_name='example'):
	return f"https://{bucket_name}.s3.amazonaws.com/{file_name}"

#-------------------------------------------------#
# Generate image using stable diffusion webui API
#-------------------------------------------------#
async def generate_image(message, neg, faces=True, steps=20, host="localhost", port=7860):
	"""
	Generate an image using the stable diffusion API.
	:param message: The prompt message to generate the image from
	:param neg: The negative prompt message to generate the image from
	:param faces: Whether to restore faces in the image
	:param steps: The number of steps to generate the image
	:param host: The host of the API
	:param port: The port of the API
	:return: The URL of the generated image
	"""
	# Generate the image by sending a POST request to the local API
	async with aiohttp.ClientSession() as session:
		# Payload
		payload = {
			"prompt": message,
			"negative_prompt": neg,
			"steps": steps,
			"cfg_scale": 7,
			"restore_faces": faces,
			"send_images": True,
			"save_images": False
		}

		# Send the POST request
		async with session.post(f'http://{host}:{port}/sdapi/v1/txt2img', json=payload) as resp:
			if resp.status == 200:
				response_json = await resp.json()
				# Get the base64 image data
				image_base64 = response_json['images'][0]

				# Check if image is safe for work
				is_safe = await check_if_safe_for_work(image_base64)
				if not is_safe:
					logger.info("Image is not safe for work, returning None & not uploading to S3")
					return None
				logger.info("Image is safe for work, continuing to upload to S3")
	
				# Decode the base64 data
				image_data = base64.b64decode(image_base64)
				# Open the image data using Pillow
				image = Image.open(io.BytesIO(image_data))

				# Generate a unique filename
				file_name = f"diffusion_{uuid.uuid4()}.png"

				# Upload the image to S3
				s3_url = await upload_to_s3(file_name, image)
				return s3_url
			else:
				print(f"Failed to generate image, status code: {resp.status}")
				return None

#-------------------------------------------------#
# Censor images from stable diffusion API
#-------------------------------------------------#

async def check_if_safe_for_work(base64_image):
	"""Returns True if image is safe for work
	:param base64_image: base64 encoded image
	:return: True if image is safe for work
	"""
	payload = {
		"input_image": base64_image,
		"enable_nudenet": True,
		"output_mask": False,
		"filter_type": "Variable blur",
		"blur_radius": 50,
		"blur_strength_curve": 3,
		"pixelation_factor": 20,
		"fill_color": "#000000",
		"mask_shape": "Ellipse",
		"mask_blend_radius": 10,
		"rectangle_round_radius": 0,
		"nms_threshold": 0.8,
		"thresholds":           [1, 1, 1, 0.5, 0.5, 1, 0.5, 1.0, 1.0, 1, 1, 1, 1, 1, 0.5, 1.0, 1.0, 1],
		"expand_horizontal":    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
		"expand_vertical":      [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
	}

	async with aiohttp.ClientSession() as session:
		async with session.post('http://localhost:7860/nudenet/censor', json=payload) as response:
			if response.status == 200:
				response_json = await response.json()
				if response_json['image'] is None:
					logger.info('This image is safe for work')
					return True
				else:
					logger.info('This image is not safe for work and was censored')
					return False
			else:
				resp_text = await response.text()
				logger.error(f"Error processing image: {resp_text}")
				return False


#-------------------------------------------------#
# Open source AI model - llamacpp
#-------------------------------------------------#
async def generate_llamacpp_response(message, sys_prompt="", host="localhost", port=8080):
	payload = {
		"model": "x",
		"messages": [
			{
				"role": "system",
				"content": sys_prompt
			},
			{
				"role": "user",
				"content": message
			}
		],
		"temperature": 0.7,
		"min_p": 0.05,
		"top_p": 400,
		"repetition_penalty": 1.30,
		"n": 1
	}

	async with aiohttp.ClientSession() as session:
		async with session.post(
			url=f"http://{host}:{port}/v1/chat/completions",
			json=payload
		) as res:
			res_json = await res.json()
			return res_json['choices'][0]['message']['content']

#-------------------------------------------------#
# Create a command tree
#-------------------------------------------------#
tree = client.tree

# Function to detect and wrap URLs in < and >
def prevent_url_embeds(text):
	# Regular expression to match URLs
	url_pattern = r'(https?://\S+)'
	return re.sub(url_pattern, r'<\1>', text)

@client.event   
async def on_ready():
	print(f'We have logged in as {client.user}')
	logger.info(f'We have logged in as {client.user}')


def split_message(message, max_length=2000):
	return [message[i:i + max_length] for i in range(0, len(message), max_length)]

@client.event
async def on_ready():
	try:
		synced = await tree.sync()
	except Exception as e:
		print(f"Failed to sync commands: {e}")
	print(f"Logged in as freebot")

# S3 configuration
S3_BUCKET = "example"
S3_PREFIX = "gotc/"  # The prefix where images are stored
BASE_URL = f"https://{S3_BUCKET}.s3.amazonaws.com/{S3_PREFIX}"


async def build_assets_map_s3():
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

			if not key.lower().endswith('.png'):
				logger.debug(f"Skipping object '{key}' because it does not end with '.png'.")
				continue

			relative_path = key[len(S3_PREFIX):]
			parts = relative_path.split('/')
			logger.debug(f"relative_path: {relative_path}, parts: {parts}")

			if len(parts) == 1:
				# Single image: "command.png"
				image_name = parts[0][:-4].lower()
				image_link = BASE_URL + parts[0]
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


def extract_inside_brackets(text):
	# Attempt to match content inside square brackets
	match = re.search(r'\[(.*?)\]', text)
	# Return the matched content or an empty string
	return match.group(1) if match else ''

#-------------------------------------------------#
# Message event
#-------------------------------------------------#
@client.event
async def on_message(message):
	logger.debug("on_message event triggered.")
	if message.author == client.user:
		logger.debug("Message author is the bot itself; ignoring.")
		return

	content_lower = message.content.lower()
	logger.debug(f"Received message: {message.content} (lower: {content_lower}) from {message.author}")

	cal_link = "https://example.s3.us-east-1.amazonaws.com/December_Calendar.png"
	if content_lower.startswith('!help'):
		logger.info(f"Calling help function for {content_lower}.")
		help_message = """
		**Gear**  
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
		- `!season#` (1-10) — Gear sets & materials for that season  
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
		- `!enhancements-nameofbuilding`
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

			**Coming Soon**
			- Hero Traits
			"""
			await message.channel.send(help_message)

	if content_lower.startswith('!image'):
		im_request = message.content.split('!image')[1].strip()
		# for neg_im_prompt grab anything inside of square [], if nothing there just return empty string
		neg_im_prompt = extract_inside_brackets(im_request)
		# now remove bracket shit from im_request
		im_request = im_request.replace('[', '').replace(']', '').replace(neg_im_prompt, '').strip()
		logger.info(f"NEG IM PROMPT: {neg_im_prompt}, IMAGE PROMPT: {im_request}")
		# FILL IN HERE
		try:
			url_image_reply = await generate_image(im_request, neg_im_prompt)
			if url_image_reply == None:
				await message.channel.send("Bummer dude, this has been censored 🙄")
			else:
				await message.channel.send(url_image_reply)
		except Exception as e:
			logger.error(f"Error generating image: {e}")
			await message.channel.send(f"Error: {e}. Please try again.")

	if content_lower.startswith('!fantasy'):
		im_request = message.content.split('!fantasy')[1].strip()
		# for neg_im_prompt grab anything inside of square [], if nothing there just return empty string
		neg_im_prompt = extract_inside_brackets(im_request)
		# now remove bracket shit from im_request
		im_request = im_request.replace('[', '').replace(']', '').replace(neg_im_prompt, '').strip()
		logger.info(f"NEG IM PROMPT: {neg_im_prompt}, IMAGE PROMPT: {im_request}")
		# FILL IN HERE
		try:
			url_image_reply = await generate_image(im_request, neg_im_prompt, faces=False, steps=30)
			if url_image_reply == None:
				await message.channel.send("Bummer dude, this has been censored 🙄")
			else:
				await message.channel.send(url_image_reply)
		except Exception as e:
			logger.error(f"Error generating image: {e}")
			await message.channel.send(f"Error: {e}. Please try again.")

	if content_lower.startswith('!calendar') or content_lower.startswith('!calender'):
		logger.info('Sending calendar')
		await message.channel.send('Here it is')
		await message.channel.send(cal_link)

	# Conditions to skip certain commands (optional)
	skip_conditions = [
		content_lower.startswith('!help'),
		content_lower.startswith('!calendar'),
		content_lower.startswith('!fu'),
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
		# await message.channel.send("Command not recognized.")
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

	# Check for calling the bot by name
	bot_called_by_name = f'{NAME_TO_CALL_BOT} ' in content_lower or f'{NAME_TO_CALL_BOT} ' in content_lower or f'{NAME_TO_CALL_BOT},' in content_lower
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
		# Correct mentions
		#--------------------------------#
		try:
			# Start with the original message content
			modified_content = message.content
			for mention in message.mentions:
				user_display_name = mention.display_name
				# Replace the mention with the display name
				modified_content = modified_content.replace(f"<@{mention.id}>", user_display_name)
			content_lower = modified_content
			logger.info(f"Content with corrected mentions: {content_lower}")
		except Exception as e:
			logger.error(f"Error processing message: {e}")
			await message.channel.send(f"I didn't understand the @ mention. Please try again: {e}")

		#--------------------------------#
		# Alternative correct mentions for username instead of display name
		#--------------------------------#
		try:
			alt_modified_content = message.content
			for mention in message.mentions:
				user_username = mention.name
				alt_modified_content = alt_modified_content.replace(f"<@{mention.id}>", user_username)
			alt_content_lower = alt_modified_content
			logger.info(f"Content with corrected mentions: {alt_content_lower}")
		except Exception as e:
			logger.error(f"Error processing message: {e}")
			await message.channel.send(f"I didn't understand the @ mention. Please try again: {e}")

		messages_list = []
		async for msg in message.channel.history(limit=6, oldest_first=False):
			# Format time as 12-hour clock with AM/PM
			timestamp = msg.created_at.strftime("%I:%M %p")
			author = msg.author.display_name
			# Remove newlines from the message content (optional)
			content = msg.content.replace('\n', ' ')

			messages_list.append(f"({timestamp}) {author}: {content}")
		messages_list.reverse()
		# Join all messages with newlines
		conversation_history_whole = "\n".join(messages_list)

		# Include the referenced bot message content if this is a reply
		reply_to_note = ""
		if bot_replied_to and referenced_message_content:
			reply_to_note = f"\n This is what you replied to the user before: '{referenced_message_content}'\n"

		reply = await generate_llamacpp_response(
			message=f"{content_lower}\n\n - For context, here is the conversation history so far: {conversation_history_whole} {reply_to_note}"
		)
		# if reply starts and ends with ", remove them
		if reply.startswith('"') and reply.endswith('"'):
			reply = reply[1:-1]
		for part in split_message(reply):
			# Prevent URL embedding in each part before sending
			part_no_embeds = prevent_url_embeds(part)
			await message.channel.send(part_no_embeds)

def main():
	# Start the bot
	client.run(os.environ['DISCORD_BOT_TOKEN'])

if __name__ == '__main__':
	main()