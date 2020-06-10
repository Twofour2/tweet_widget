import praw
import praw.models.util
import psycopg2
import configparser
import os
import time
import logging
script_dir = os.path.dirname(__file__)  # get where the script is
logging.basicConfig(filename=script_dir+'/logs/twitterBotMsg.log',level=logging.INFO, format='%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S')

# TWITTER WIDGET V3
# by /u/chaos_a
# Checks messages for the bot account

def Main():
    botconfig = configparser.ConfigParser()
    botconfig.read(script_dir + "/botconfig.ini")
    global conn2
    conn2 = dbConnect(botconfig)
    r = redditlogin(botconfig)
    checkMail(r)

def checkMail(r):
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

def acceptModInvite(message):
    try:
        logging.info("Accepting mod invite for subreddit %s" % message.subreddit.display_name)
        global conn2
        cur = conn2.cursor()
        message.mark_read()
        message.subreddit.mod.accept_invite()

        cur.execute(
            "SELECT * FROM subreddits WHERE subname=%s",
            (str(message.subreddit).lower(),),
        )
        results = cur.fetchall()
        if results:
            cur.execute(
                "UPDATE subreddits SET enabled=True WHERE subname=%s",
                (str(message.subreddit).lower(),),
            )
            logging.info("Re-enabling subreddit %s" % message.subreddit.display_name)
        else:
            cur.execute(
                "INSERT INTO subreddits (subname) VALUES(%s)",
                (str(message.subreddit).lower(),),
            )
            logging.info("Successfully added subreddit %s to database" % message.subreddit.display_name)
        logging.warning("Accepted invite for /r/%s" % message.subreddit.display_name)
    except Exception as e:
        logging.warning("Error: %s" % e)

def removeModStatus(message):
    try:
        global conn2
        cur = conn2.cursor()
        message.mark_read()
        cur.execute(
            "UPDATE subreddits SET enabled=False WHERE subname=%s",
            (str(message.subreddit).lower(),),
        )
        logging.info("Set enabled to false for subreddit %s"%message.subreddit.display_name)
    except Exception as e:
        logging.warning("Error: %s"%e)

def createConfig(subreddit): # create the config file
    try:
        subreddit.wiki.create(name='twittercfg', content='#Twitter feed bot config\n---  \nenabled: True  \nmode: user')
    except Exception as e: # already exists
        logging.warning("Error: Config already exists, recieved error %s"%e)
        return

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
        logging.warning("Could not connect to database: %s" % e)
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
        logging.warning("Could not connect to reddit: %s" % e)
        time.sleep(120)

if __name__ == "__main__":
    Main()