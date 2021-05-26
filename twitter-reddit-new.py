import praw
import prawcore
import configparser
import logging
import tweepy
from datetime import datetime, timedelta
import psycopg2
import time
import os
import sys
import subprocess
import timeout_decorator
from twsubreddit import twSubreddit

script_dir = os.path.split(os.path.realpath(__file__))[0]  # get where the script is
logging.basicConfig(filename=script_dir+'/logs/twitterBotMain.log',level=logging.INFO, format='%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S')
nextImageUploadDate = None

# Twitter Widget v4
# by /u/chaos_a

def Main():
    botconfig = configparser.ConfigParser()
    botconfig.read(script_dir + "/botconfig.ini")

    global nextImageUploadDate
    uploadImages = False
    for arg in sys.argv:
        if arg in ("-t", "-test"):
            testMode = True
            continue
        else:
            logging.info("--------Starting Twitter Bot--------")

    if os.name == "nt":
        twSubreddit.zeroPaddingSymbol = "#"

    testMode = True
    global tApi
    global conn2
    conn2 = dbConnect(botconfig)
    twSubreddit.conn2 = conn2
    cur = conn2.cursor()
    twSubreddit.cur = cur

    tAuth = tweepy.OAuthHandler(botconfig.get("twitter", "APIKey"), botconfig.get("twitter", "APISecret"))
    tAuth.set_access_token(botconfig.get("twitter", "AccessToken"), botconfig.get("twitter", "TokenSecret"))
    tApi = tweepy.API(tAuth)
    twSubreddit.tApi = tApi

    if testMode:
        logging.info("Test mode")
        cur.execute("SELECT * FROM subreddits_testing")
    results = cur.fetchall()
    allSubreddits = []
    reddit = redditlogin(botconfig)

    for subredditData in results:
        # (Subname, enabled, latest, last_gather, last_update
        if subredditData[1]: # dont bother if the subreddit is not enabled
            subreddit = twSubreddit(subredditData, reddit) # generate new subreddit object
            logging.info(f"Adding {subreddit}")
            allSubreddits.append(subreddit)
    logging.info("Done loading subreddits")
    while True:

        for twSub in allSubreddits:
            try:
                twSub.loadConfig()
                if datetime.utcnow().timestamp() > twSub.nextImageUploadTimestamp or twSub.configChanged:
                    logging.info(f"Uploading images to subreddit {twSub.Name}")
                    twSub.uploadImages()
                    twSub.bugFixImageUpload = True
                    twSub.nextImageUploadDate = (datetime.utcnow() + timedelta(days=1)).timestamp()
                    logging.info(f"Done uploading images to {twSub.Name}, next upload is scheduled for: {datetime.fromtimestamp(twSub.nextImageUploadTimestamp)}")
                twSub.updateWidget()

            except timeout_decorator.TimeoutError as e:
                twSub.logFailure(f"{twSub.Name}: Timed out ({e})", exception=e)
            except Exception as e:
                twSub.logFailure(f"{twSub.Name}: Other outer exception: {e}", exception=e)
        else:
            logging.info("Done with widgets, waiting 5 mins")
            time.sleep(300)

def dbConnect(botconfig):
    # DB Connection
    dbName = botconfig.get("database", "dbName")
    dbPasswrd = botconfig.get("database", "dbPassword")
    dbUser = botconfig.get("database", "dbUsername")
    dbHost = botconfig.get("database", "dbHost")
    try:
        global conn2
        conn2 = psycopg2.connect(  # connect
            "dbname='{0}' user='{1}' host='{2}' password='{3}'".format(
                dbName, dbUser, dbHost, dbPasswrd
            )
        )
        conn2.autocommit = True
        return conn2
    except Exception as e:  # could not connect
        logging.error("Cannot connect to database")
        time.sleep(120)

def redditlogin(botconfig):
    # reddit login
    try:
        r = praw.Reddit(client_id=botconfig.get("reddit", "clientID"),
                        client_secret=botconfig.get("reddit", "clientSecret"),
                        password=botconfig.get("reddit", "password"),
                        user_agent=botconfig.get("reddit", "useragent"),
                        username=botconfig.get("reddit", "username"))
        return r  # return reddit instance
    except Exception as e:  # reddit is down
        logging.error("Reddit/PRAW Issue, site may be down")
        time.sleep(120)

if __name__ == "__main__":
    Main()