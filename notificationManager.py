import requests
from datetime import datetime
import configparser
import os
import praw
import time


def sendNotif(botconfig, message, pushNotif):
    """Sends a message to a discord server channel"""
    if botconfig.has_section("notification"): # check if enabled
        HEADERS = {'Authorization': "Bot {}".format(botconfig.get('notification', 'APIKey')),
                   'user-agent': 'DiscordBot (https://discordapp.com/api/), 1.0)',
                   'content-type': 'application/json'}
        URLSend = "https://discordapp.com/api/channels/{}/messages".format(
            botconfig.get("notification", "SendChannelID"))
        URLMute = "https://discordapp.com/api/channels/{}/messages".format(
            botconfig.get("notification", "MuteChannelID"))

        # check if latest message is not !mute
        r = requests.get(url=URLMute, headers=HEADERS, params={"limit":2})
        for msg in r.json():
            if msg['content'] == "!mute":
                return "Bot is muted." # mute the bot
        else:
            if pushNotif: # add @everyone
                message = "@everyone: {}".format(message)
            requests.post(url=URLSend, headers=HEADERS, json={"content": message, "tts": 'false'})

def checkSubredditLogs(botconfig, reddit):
    """Check if tweet widget is still posting to a subreddit"""
    for log in reddit.subreddit(botconfig.get("reddit", "logCheckSubreddit")).mod.log(limit=25):
        if log.mod == "tweet_widget":
            timeDiff = datetime.now() - datetime.fromtimestamp(log.created_utc)
            if timeDiff.seconds < 3600: # log is under an hour old
                return True
    else:
        return False

def checkStatus():
    """Run the above function to check if the bot is active"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    botconfig = configparser.ConfigParser()
    botconfig.read(script_dir + "/botconfig.ini")
    if not checkSubredditLogs(botconfig, redditlogin(botconfig)): # bot has not posted in the last hour
        sendNotif(botconfig, "Check status has detected that the bot is not active! Log Check Subreddit: {}".format(botconfig.get("reddit", "logCheckSubreddit")), True)

def redditlogin(botconfig):
    # reddit login
    try:
        r = praw.Reddit(client_id=botconfig.get("reddit", "clientID"),
                        client_secret=botconfig.get("reddit", "clientSecret"),
                        password=botconfig.get("reddit", "password"),
                        user_agent=botconfig.get("reddit", "useragent"),
                        username=botconfig.get("reddit", "username"))
        return r
    except Exception as e:
        time.sleep(120)

if __name__ == "__main__":
    checkStatus() # run this only via cron


