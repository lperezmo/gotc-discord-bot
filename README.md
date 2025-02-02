# A simple AI discord bot

A simple AI discord bot

    * Summarizing channel conversations up to x days, y hours ago
    * Answer using embeddings when needed
    * Use web search as well, through pypi's duckduckgo_search, Bing Search API, or Google Search API (first one is free)
    * Return diagrams images to given commands
    * Generate images
    * Can be run locally, or a combination of both

### Simple setup on a local computer/server:
1. Create your discord bot, obtain its token, and add to your server (or test server) as described here: 
    * https://discordpy.readthedocs.io/en/stable/discord.html

1. Clone repo
    ```bash
    git clone https://github.com/lperezmo/gotc-discord-bot.git
    cd gotc-discord-bot
    ```

2. *(Recommended)* Install miniconda, create virtual env and install required packages
    ```bash
    conda create -n discord python==3.9 -y
    pip install -r requirements.txt
    ```

3. *(Alternative)* Just make sure you have packages installed on your python environment of choice

4. *(Optional)* Set up llama-cpp (or llamafile) and stable diffusion webui. Download models. Enable API access.

4. Rename `.env-example` to `.env` and replace example env variables with yours.

5. Navigate to the folder where app is stored, start, & talk to your new AI bot
    ```bash
    python ./app.py
    ```
    or 
    ```bash
    python ./open-app.py
    ```
    for the open-source version

### Freebot
1. Generate replies using stable diffusion webui + llamacpp. Completely free. 
2. There are plenty of guides to get both of them working, so I won't go into detail here, but once you have the txt2txt and txt2image endpoints working you can go ahead and plug into your bot and it will run entirely free.

### OpenAI-based bot
1. This one uses OpenAI's. You can edit the models used, instructions, etc. 
2. Note that this example heavily relies on json mode (where custom replies with valid JSON, always) to route different replies, 

### Citadel diagrams
1. These are in `data/gotc` folder on this repo. Upload + edit your bot to call them from S3, github, google drive, as attachment, or other hosting platform.