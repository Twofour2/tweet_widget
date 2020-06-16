import requests
from datetime import datetime
import configparser
import os
import praw
import time
import psycopg2
import threading

script_dir = os.path.dirname(os.path.abspath(__file__))
botconfig = configparser.ConfigParser()
botconfig.read(script_dir + "/botconfig.ini")
HEADERS = {'Authorization': "Bot {}".format(botconfig.get('notification', 'APIKey')),
                   'user-agent': 'DiscordBot (https://discordapp.com/api/), 1.0)',
                   'content-type': 'application/json'}

def sendLog(message):
    """Sends warning logs"""
    if botconfig.has_section("notification"):
        channelID = botconfig.get("notification", "LogChannelID")
        HEADERS = {'Authorization': "Bot {}".format(botconfig.get('notification', 'APIKey')),
                   'user-agent': 'DiscordBot (https://discordapp.com/api/), 1.0)',
                   'content-type': 'application/json'}
        channelURL = "https://discordapp.com/api/channels/{}/messages".format(channelID)
        muteURL = "https://discordapp.com/api/channels/{}/messages".format(
            botconfig.get("notification", "MuteChannelID"))

        # check if latest message is not !mute
        r = requests.get(url=muteURL, headers=HEADERS, params={"limit": 2})
        for msg in r.json():
            if msg['content'] == "!mute":
                return "Bot is muted."  # mute the bot
        else:
            # send the log
            requests.post(url=channelURL, headers=HEADERS,json={"content": message, "tts": 'false'})

def sendStatus(message, pushNotif, channelID):
    """Send a message that edits, rather than keeping a constant log"""
    if not channelID:
        # default to logging channel if not provided
        channelID = botconfig.get("notification", "SendChannelID")
    if botconfig.has_section("notification"):
        channelURL = "https://discordapp.com/api/channels/{}/messages".format(channelID)
        muteURL = "https://discordapp.com/api/channels/{}/messages".format(
            botconfig.get("notification", "MuteChannelID"))

        if pushNotif: # send a urgent new message
            # check if latest message is not !mute
            r = requests.get(url=muteURL, headers=HEADERS, params={"limit": 2})
            for msg in r.json():
                if msg['content'] == "!mute":
                    return "Bot is muted."  # mute the bot
            else:
                # this will always send a new message on purpose
                requests.post(url=channelURL, headers=HEADERS, json={"content": f"<@&720441592191909979> : {message}", "tts": 'false'})
        else: # normal status message, just edit the old one if it exists
            r = requests.get(url=channelURL, headers=HEADERS, params={"limit": 2})
            MessageData = r.json()[0] # first message
            if MessageData['author']['username'] == botconfig.get("notification", "BotName"):
                if not str(MessageData['content']).startswith("<@"): # prevent override of @ messages
                    # last message was by bot, so edit it
                    r = requests.patch(url=channelURL+f"/{MessageData['id']}", headers=HEADERS, json={"content": message})
                else:
                    # create a new message
                    requests.post(url=channelURL, headers=HEADERS, json={"content": message, "tts": 'false'})
            else:
                # create a new message
                requests.post(url=channelURL, headers=HEADERS, json={"content": message, "tts": 'false'})

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
    if not checkSubredditLogs(botconfig, redditlogin(botconfig)): # bot has not posted in the last hour
        sendStatus("Check status has detected that the bot is not active! Log Check Subreddit: {}".format(botconfig.get("reddit", "logCheckSubreddit")), True)
        sendStatus(f"Status: Inactive\nLast Checked on: UTC {datetime.utcnow()}", False, botconfig.get("notification", "StatusChannelID"))
    else: # bot is alive
        sendStatus(f"Status: Active\nLast Checked on: UTC {datetime.utcnow()}", False, botconfig.get("notification", "StatusChannelID"))

def checkCommands():
    conn2 = dbConnect(botconfig)
    muteURL = "https://discordapp.com/api/channels/{}/messages".format(
        botconfig.get("notification", "MuteChannelID"))

    # get msg
    rMsg = requests.get(url=muteURL, headers=HEADERS, params={"limit": 1})
    for msg in rMsg.json():
        channelID = msg['channel_id']
        serverID = botconfig.get("notification", "ServerID")
        r = requests.get(url=f"https://discordapp.com/api/guilds/{serverID}/members/{msg['author']['id']}", headers=HEADERS)
        if botconfig.get("notification", "AdminRoleID") in r.json()['roles']: # user has the admin role
            content = str(msg['content'])
            if content.startswith("!disable"): # disable a subreddit
                idx = content.index(" ") + 1
                subname = content[idx:]
                cur = conn2.cursor()
                cur.execute(
                    "UPDATE subreddits SET enabled=False WHERE subname=%s",
                    (subname.lower(),),
                )
            elif content.startswith("!enable"): # enable a subreddit
                idx = content.index(" ") + 1
                subname = content[idx:]
                cur = conn2.cursor()
                cur.execute(
                    "UPDATE subreddits SET enabled=True WHERE subname=%s",
                    (subname.lower(),),
                )
            elif content.startswith("!data"): # list all database data
                cur = conn2.cursor()
                cur.execute("SELECT * FROM subreddits")
                res = cur.fetchall()
                sendStatus("All database info: {}".format(res), False, channelID)
            elif content.startswith("!list"): # list all subreddits
                cur = conn2.cursor()
                cur.execute("SELECT subname, enabled FROM subreddits")
                res = cur.fetchall()
                sendStatus("All tweet_widget subreddits: {}".format(res), False, channelID)
            elif content.startswith("!logs"): # read out logs
                with open(script_dir+"/logs/twitterBot.log", "r") as f:
                    sendLog(str(f.read()))
                    sendStatus("Sent log file to warnings channel", False, channelID)
            else:
                sendStatus("Unknown command, valid commands are: [!disable, !enable, !data, !list, !logs]", False, channelID)
        else:
            sendStatus("You are not admin!", False, channelID)

def dbConnect(botconfig):
    # DB Connection
    dbName = botconfig.get("database", "dbName")
    dbPasswrd = botconfig.get("database", "dbPassword")
    dbUser = botconfig.get("database", "dbUsername")
    dbHost = botconfig.get("database", "dbHost")
    try:
        global conn2
        conn2 = psycopg2.connect(
            "dbname='{0}' user='{1}' host='{2}' password='{3}'".format(
                dbName, dbUser, dbHost, dbPasswrd
            )
        )
        conn2.autocommit = True
        return conn2
    except Exception as e:
        time.sleep(120)

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
    while True:
        checkStatus() # update the status
        checkCommands() # check for any new commands
        time.sleep(300) # wait 5 mins



