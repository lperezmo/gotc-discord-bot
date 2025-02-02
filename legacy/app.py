import os
import base64
import discord
import logging
import numpy as np
import pandas as pd
from time import sleep
from openai import OpenAI
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler
from sklearn.metrics.pairwise import cosine_similarity

#-------------------------------------------------#
# Env variables if using .env file
#-------------------------------------------------#
load_dotenv()

#-------------------------------------------------#
# Rotating log handler
#-------------------------------------------------#
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# Create logs directory if it doesn't exist
if not os.path.exists('logs'):
    os.makedirs('logs')

# Set up the rotating file handler with a 10 MB maximum file size
rotating_handler = RotatingFileHandler('logs/discord_bot.log', mode='a', maxBytes=10*1024*1024, backupCount=25)
rotating_handler.setLevel(logging.INFO)
rotating_handler.setFormatter(logging.Formatter(LOG_FORMAT))

# Set up the root logger and add the rotating file handler
logging.basicConfig(level=logging.INFO,
					format=LOG_FORMAT,
					handlers=[rotating_handler])

# Create a logger for the module and set it to propagate to the root logger
logger = logging.getLogger('discord_bot')

#-------------------------------------------------#
# OpenAI API Client
#-------------------------------------------------#
ai_client = OpenAI()
# For llama-cpp (open-source alternative) use:
# Source: https://llama-cpp-python.readthedocs.io/en/latest/server/
# client = OpenAI(base_url="http://<host>:<port>/v1", api_key="sk-madeupkey")

#-------------------------------------------------#
# Discord Client
#-------------------------------------------------#
intents = discord.Intents.default()
intents.message_content = True  # Enable the message content intent
client = discord.Client(intents=intents)

#-------------------------------------------------#
# GOTC Embeddings
#-------------------------------------------------#
df_embeddings = pd.read_parquet('data/embeddings_gotc.parquet')

#-------------------------------------------------#
# Functions
#-------------------------------------------------#
async def get_top_k_results_text(df, query_text, embed_model='text-embedding-3-small', n=3):
    """
    Get the top-k results from the dataframe based on the cosine similarity of the embeddings
    of the query text and the text in the dataframe.
    Params:
    - df: The dataframe containing the text and embeddings
    - query_text: The query text
    - embed_model: The embedding model to use
    - n: The number of top results to return
    Returns:
    - joined_text: The joined text of the top-k results
    - sources: The sources of the top-k results
    """
    # create embeddings (try-except added to avoid RateLimitError)
    # Added a max of 5 retries
    max_retries = 5
    retry_count = 0
    done = False

    while not done and retry_count < max_retries:
        try:
            res = ai_client.embeddings.create(input=query_text, model=embed_model)
            done = True
        except Exception as e:
            print(f"Error creating embeddings for batch {e}")
            retry_count += 1
            sleep(5)
    query_embedding = res.data[0].embedding

    # Compute cosine similarity
    similarities = cosine_similarity([query_embedding], list(df['embedding']))
    
    # Find top-k indices and metadata
    top_k_indices = np.argsort(similarities[0])[-n:][::-1]
    top_k_results = df.iloc[top_k_indices]

    # Join the text of the top-k results
    joined_text = ' '.join(list(top_k_results['text']))
    sources = list(top_k_results['source'])

    return joined_text, sources

async def retrieve(query, df, limit_of_context = 3750, embed_model = 'text-embedding-3-small'):
    """
    Retrieve additional context based on the query and the dataframe.
    Params:
    - query: The query text
    - df: The dataframe containing the text and embeddings
    - limit_of_context: The maximum number of characters to return
    - embed_model: The embedding model to use
    Returns:
    - prompt: The prompt to use for the completion
    """
    # get relevant contexts
    contexts, sources = await get_top_k_results_text(df, query, embed_model=embed_model, n=3)

    # Limit the number of characters
    contexts = contexts[:limit_of_context]

    # build our prompt with the retrieved contexts included
    prompt = (
        f"The following is additional context that might help in answering the users query.\n\n"+
        f"Context:\n {contexts}\n\n Sources: {sources} Question: {query}\nAnswer:"
    )
    return prompt

async def process_message_with_images(message, list_of_image_urls):
    """
    Process a message with images.
    Params:
    - message: The message text
    - list_of_image_urls: A list of image URLs
    Returns:
    - reply: The reply message
    """
    global df_embeddings
    system_prompt = """Your task is summarize the user request to generate additional context for this question"""
    sleep(1)
    #-------------------------------------------------#
    # Generate a rephrased question based on the images
    # and prompt.
    #-------------------------------------------------#
    # Create message content
    message_content = [{"type": "text", "text": f"Based on the following text, summarize the user request to generate additional context based on the images + prompt: {message}"}]
    
    # Add each image URL to the message content
    for image_url in list_of_image_urls:
        message_content.append({
            "type": "image_url",
            "image_url": {
                "url": f"{image_url}"
            }
        })
    
    # Make the API call with the updated message content
    additional_context = ai_client.chat.completions.create(
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
    
    #-------------------------------------------------#
    # Regenerate message content & reponse based on 
    # rephrased question.
    #-------------------------------------------------#
    system_prompt = """You are a friendly and helpful assistant that answers questions about
    the mobile game 'Game of Thrones: Conquest'. Keep your responses short, informative,
    and don't try too much."""
    augmented_text = await retrieve(query=f"""{additional_context}""", df=df_embeddings)
    message_content = [{"type": "text", "text": f"Original question: {message}. \n\n Additional context that you might or might not need (ignore if not relevant): {augmented_text}"}]
    
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

async def process_message(message):
    """
    Process a message.
    Params:
    - message: The message text
    Returns:
    - reply: The reply message
    """
    # Combine message with image
    global df_embeddings
    system_prompt = """You are friendly and helpful assistant that answers questions about
    the mobile game 'Game of Thrones: Conquest'. Keep your responses short, informative,
    and please dont try too hard. Provide links to sources in given context if anything."""
    augmented_text = await retrieve(query=message, df=df_embeddings)
    res = ai_client.chat.completions.create(
						model="gpt-4o-mini",
						messages=[
                            {"role": "system","content": f"{system_prompt}"},
							{"role": "user","content": f"{augmented_text}"}
						],
					)
    logger.info(f"Response: {res}")
    return res.choices[0].message.content

@client.event   
async def on_ready():
    """
    Event handler for when the bot is ready.
    """
    print(f'We have logged in as {client.user}')
    logger.info(f'We have logged in as {client.user}')

@client.event
async def on_message(message):
    """
    Event handler for when a message is received.
    Params:
    - message: The message object
    Returns:
    - None
    """
    if message.author == client.user:
        return
    content_lower = message.content.lower()
    # 
    if 'firebot' in content_lower:
        print("The words 'firebot' were mentioned in the message!")
        
        # Check if the message has any attachments
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
                # Process message with all image URLs
                reply = await process_message_with_images(message.content, image_urls)

                # Function to split message if it exceeds the Discord character limit
                def split_message(message, max_length=2000):
                    return [message[i:i + max_length] for i in range(0, len(message), max_length)]

                # Send message in parts if necessary
                for part in split_message(reply):
                    await message.channel.send(part)

        else:
            # Process message without a picture
            logger.info(f"Processing message: {message.content}")
            reply = await process_message(message.content)
            await message.channel.send(reply)

def main():
    """
    Main function to run the Discord bot.
    """
    client.run(os.getenv('DISCORD_BOT_TOKEN'))

if __name__ == '__main__':
    main()

