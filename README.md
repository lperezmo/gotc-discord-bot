# GOT Conquest AI Discord Bot

<img src="https://www.gotconquest.com/app/themes/got/dist/images/gotc_hotd_logo.png" alt="GOTC HOTD Logo" width="200" height="100">

A simple AI discord bot that uses embeddings to provide some answers about gotc

### Simple setup on a local computer/server:
1. Create your discord bot, obtain its token, and add to your server (or test server) as described here: 
    * https://discordpy.readthedocs.io/en/stable/discord.html

1. Clone repo
    ```bash
    git clone https://github.com/lperezmo/gotc-discord-bot.git
    cd gotc-discord-bot
    ```

2. (Recommended) Install miniconda, create virtual env and install required packages
    ```bash
    conda create -n discord python==3.9 -y
    pip install -r requirements.txt
    ```

3. (Alternative) Just make sure you have packages installed on your python environment of choice

4. Rename `.env-example` to `.env` and replace example env variables with yours.

5. Run & talk to your new AI bot
    ```bash
    python ./app.py
    ```

### Future plans
1. Taking advantage of OpenAI's tool-calling functionality to have bot call different tools, including:
    * Summarizing channel conversations up to x days, y hours ago
    * Getting context from embeddings when needed only
    * Use web search as well, through pypi's duckduckgo_search, Bing Search API, or Google Search API (first one is free)
    * Downloading diagrams from Citadel discord and transcribe (using AI or OCR) and add to bot's embeddings.