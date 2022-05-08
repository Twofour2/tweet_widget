############################################################################
## Django ORM Standalone
############################################################################

# Turn off bytecode generation
import sys
sys.dont_write_bytecode = True

# Django specific settings
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
import django
django.setup()

# Import your models for use in your script
from db.models import *

import praw
import praw.models.util
import prawcore
import inspect
import psycopg2
import configparser
import os
import time
import logging
script_dir = os.path.split(os.path.realpath(__file__))[0] # get where the script is
logging.basicConfig(filename=script_dir+'/logs/twitterBotMsg.log',level=logging.INFO, format='%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S')

# TWITTER WIDGET V5
# by chaos_a
# Checks messages for the bot account

def Main():
    while True:
        try:
            botconfig = configparser.ConfigParser()
            botconfig.read(script_dir + "/botconfig.ini")
            r = redditlogin(botconfig)
            checkMail(r)
        except Exception as e:
            logging.warning(f"An exception occurred: {e}")


def checkMail(r):
    try:
        for msg in praw.models.util.stream_generator(r.inbox.unread): # stream unread messages
            if not isinstance(msg, praw.models.Message):
                msg.mark_read()
                continue
            logging.info("Got message %s" % msg.body)
            if (
                msg.body.startswith("**gadzooks!")
                or msg.body.startswith("gadzooks!")
                or msg.subject.startswith("invitation to moderate")
            ):
                acceptModInvite(msg)
                createConfig(msg.subreddit) # create the config file
                msg.mark_read()
                continue
            if msg.subject.strip().lower().startswith("moderator message from"):
                msg.mark_read()
                continue
            if "You have been removed as a moderator from" in msg.body:
                logging.info("Removing self from subreddit %s"%msg.subreddit.display_name)
                removeModStatus(msg)
                continue

            else:
                msg.mark_read()
                continue
    except prawcore.ServerError as e:
        logging.error(f"Server error: {e}")
    except prawcore.RequestException as e:
        logging.error(f"Request Exception: {e}")
    except Exception as e:
        logging.error(f"An exception occurred: {e}")


def acceptModInvite(message):
    try:
        logging.info("Accepting mod invite for subreddit %s" % message.subreddit.display_name)
        message.mark_read()
        message.subreddit.mod.accept_invite()
        Subreddit.objects.create(subname=str(message.subreddit).lower(), enabled=True)
        print('created object')
        logging.info("Successfully added subreddit %s to database" % message.subreddit.display_name)
        logging.warning("Accepted invite for /r/%s" % message.subreddit.display_name)
    except Exception as e:
        logging.warning("Error: %s" % e)

def removeModStatus(message):
    try:
        message.mark_read()
        sub = Subreddit.objects.filter(subname=str(message.subreddit).lower())
        sub.delete()
        logging.info("Set enabled to false for subreddit %s"%message.subreddit.display_name)
    except Exception as e:
        logging.warning("Error: %s"%e)

def createConfig(subreddit): # create the config file
    try:
        subreddit.wiki.create(name='twittercfg', content='---  \nenabled: false  \nuser: Twitter  \n#list: (put list id here)')
    except Exception as e: # already exists
        logging.warning("Error: Config already exists, recieved error %s"%e)
        return

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
        logging.warning("Could not connect to reddit: %s" % e)
        time.sleep(120)

if __name__ == "__main__":
    Main()