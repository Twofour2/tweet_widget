import requests
from datetime import datetime

def sendNotif(botconfig, message, pushNotif):
    """Sends a message to a discord server channel"""
    if botconfig.has_section("notification"): # check if enabled
        HEADERS = {'Authorization': "Bot {}".format(botconfig.get('notification', 'APIKey').strip('\"')),
                   'user-agent': 'DiscordBot (https://discordapp.com/api/), 1.0)',
                   'content-type': 'application/json'}
        URL = "https://discordapp.com/api/channels/{}/messages".format(
            botconfig.get("notification", "channelID").strip('\"'))

        # check if latest message is not !mute
        r = requests.get(url=URL, headers=HEADERS, params={"limit":2})
        for msg in r.json():
            if msg['content'] == "!mute":
                return "Bot is muted." # mute the bot
        else:
            if pushNotif: # add @everyone
                message = "@everyone: {}".format(message)
            requests.post(url=URL, headers=HEADERS, json={"content": message, "tts": 'false'})

def checkSubredditLogs(botconfig, reddit):
    """Check if tweet widget is still posting to a subreddit"""
    for log in reddit.subreddit(botconfig.get("reddit", "logCheckSubreddit")).mod.log(limit=25):
        if log.mod == "tweet_widget":
            timeDiff = datetime.now() - datetime.fromtimestamp(log.created_utc)
            if timeDiff.seconds < 3600: # log is under an hour old
                return True
    else:
        return False

