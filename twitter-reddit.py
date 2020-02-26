import praw
import prawcore
import configparser
import logging
import traceback
import tweepy
import re
import yaml
import pprint
from datetime import datetime, timezone
import psycopg2
import time
import sys
import os
logging.basicConfig(filename='./logs/twitterBot.log',level=logging.INFO, format='%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S')
#
# TWITTER WIDGET V3
# by /u/chaos_a
# creates a easy to use twitter feed for subreddits

def Main():
    logging.info("--------Starting Twitter Bot--------")
    script_dir = os.path.dirname(os.path.abspath(__file__))  # get where the script is
    botconfig = configparser.ConfigParser()
    botconfig.read(script_dir + "/botconfig.ini")
    print(script_dir+"/botconfig.ini")
    while True: # run this part forever
        # twitter auth
        auth = tweepy.OAuthHandler(botconfig.get("twitter", "APIKey"), botconfig.get("twitter", "APISecret"))
        auth.set_access_token(botconfig.get("twitter", "AccessToken"), botconfig.get("twitter", "TokenSecret"))
        global tApi
        tApi = tweepy.API(auth)
        reddit = redditlogin(botconfig)
        global conn2
        conn2 = dbConnect(botconfig)
        cur = conn2.cursor()
        cur.execute("SELECT * FROM subreddits")
        results = cur.fetchall()

        for subredditdata in results: # go through every subreddit
            logging.info("Checking tweets for subreddit %s" % subredditdata[0])
            if subredditdata[1] == True: # bot is enabled for this subreddit via database
                subreddit = reddit.subreddit(subredditdata[0]) # set the subreddit
                try:
                    wiki = subreddit.wiki['twittercfg'].content_md # get the config wiki page
                    config = yaml.load(wiki, Loader=yaml.FullLoader) # load it
                    if config: # if the file actually works
                        valid = checkCfg(subreddit, config) # validate that everything is correct
                        if valid == True:
                            if config['enabled'] == True: # bot is enabled via config
                                try:
                                    getTweets(subreddit, config, subredditdata) # get new tweets
                                except Exception as e:
                                    logging.warning(
                                        "An error occurred while checking tweets on subreddit {}: {}".format(subredditdata[0], e))
                        else:
                            logging.warning("Bad config file on subreddit %s" % subreddit.display_name)
                    else:
                        logging.warning("BROKEN CONFIG FILE on subreddit %s" % subreddit.display_name)

                except prawcore.exceptions.NotFound:
                    subreddit.wiki.create(name='twittercfg', content='---  \nenabled: false  \nmode: user')
                    logging.info("Created wiki page on subreddit %s" % subreddit.display_name)
            else:
                logging.info("Subreddit %s is disabled" % subredditdata[0])
        logging.info("Done with tweets, sleeping for 5 mins")
        time.sleep(300)


def getTweets(subreddit, config, subredditdata):
    count = 10
    if 'mode' in config:
        mode = config['mode'] # get current mode
    else:
        sendWarning(subreddit, "Config Error: Missing mode type (list/user)")
        return
    if 'count' in config:
        count = int(config['count']) # get tweet count
        if count > 15: # enforce limit
            count = 15
    if mode == 'user': # get tweets from a single user
        user = config['screen_name']
        Tweets = tApi.user_timeline(screen_name=user, count=count, tweet_mode='extended', include_entities=True)  # get first tweets id number
        if checkLatest(Tweets, subredditdata):
            MakeMarkupUser(Tweets, subreddit, config, mode) # use the user markup function
    elif mode == 'list': # get tweets by many users via a list
        Tweets = tApi.list_timeline(owner_screen_name=config['owner'], slug=config['list'], count=count, tweet_mode='extended', include_entities=True)
        if checkLatest(Tweets, subredditdata):
            MakeMarkupList(Tweets, subreddit, config, mode) # use the list markup function

def checkLatest(Tweets, subredditdata): # checks if the latest tweet is in the database, meaning that it is already in the widget
    global conn2
    try:
         t = Tweets[0] # get the latest tweet
         if subredditdata[2] == t.id_str: # id's do match
             return False # do not update the widget
         else: # id's do not match, includes "None"
             cur = conn2.cursor()
             cur.execute("UPDATE subreddits SET latest={} WHERE subname='{}'".format(t.id_str, subredditdata[0]))  # store the new latest tweet id
             return True # do update the widget
    except Exception as e:
         logging.warning("An error occurred while checking latest status on subreddit {}: {}".format(subredditdata[0], e))
         return False


def MakeMarkupUser(Tweets, subreddit, config, mode): # twitter user mode
    try:
        if 'title' in config:
            markup = ("#{}\n".format(config['title'])) # custom title
        else: # default title
            markup = ("#Tweets\n")
        for t in Tweets:
            json = t._json
            hotlinkFormat = "https://www.twitter.com/{0}/status/{1}".format(json['user']['screen_name'], json['id']) # format a link to the tweet with username and tweet id
            timestampStr = convertTime(t.created_at)
            profileUrl = "https://www.twitter.com/"  # this + username gives a link to the users profile
            tweet_text = tweetFormatting(t, t.full_text)
            fulltext =  tweet_text.replace("\n", "\n>") # add the '>' character for every new line so it doesn't break the quote
            # MARKUP NOTE: 2 hashes are used here to signal %%profile1%%
            markup += ("\n\n---\n##**[{} *@{}*]({})**   \n[{}]({}) \n>{}".format(t.user.name, t.user.screen_name, profileUrl+t.user.screen_name.lower(), timestampStr, hotlinkFormat,fulltext))
            if 'show_retweets' in config: # add re-tweet info
                if config['show_retweets'] == True:
                    markup += ("\n\n>**{}** Retweets  **{}** Likes".format(t.retweet_count, t.favorite_count))
        else: # once markup is done
            insertMarkup(subreddit, markup, config, mode) # put it on the subreddit
    except Exception as e:
        logging.warning("An error occurred while making the markup on subreddit {}: {}".format(subreddit.display_name, e))

def MakeMarkupList(Tweets, subreddit, config, mode): # twitter list mode
    global timezone
    try:
        if 'title' in config:
            markup = ("#{}\n".format(config['title'])) # custom title
        else: # default title
            markup = ("#Tweets\n")
        profileUrl = "https://www.twitter.com/"  # this + username gives a link to the users profile
        userhashes = {k.casefold(): v for k, v in config['users'].items()}  # make all dict items lowercase
        # FORMATTING INFO: Userhashes (above) is used to calculate which header value is used (h2-h6)
        # the rest is css magic
        for t in Tweets:
            json = t._json
            hotlinkFormat = "https://www.twitter.com/{0}/status/{1}".format(json['user']['screen_name'], json['id']) # format a link to the tweet with username and tweet id
            timestampStr = convertTime(t.created_at)
            tweet_text = tweetFormatting(t, t.full_text)
            fulltext =  tweet_text.replace("\n", "\n>") # add the '>' character for every new line so it doesn't break the quote
            markup += ("\n\n---\n{}**[{} *@{}*]({})**   \n[{}]({}) \n>{}".format(('#'*(userhashes[t.user.screen_name.lower()]+1)), t.user.name, t.user.screen_name, profileUrl+t.user.screen_name.lower(), timestampStr, hotlinkFormat, fulltext))

            if 'show_retweets' in config:
                if config['show_retweets'] == True: # add re-tweet info
                    markup += ("\n\n>**{}** Retweets  **{}** Likes".format(t.retweet_count, t.favorite_count))
        else: # once markup is done
            insertMarkup(subreddit, markup, config, mode) # put it on the subreddit
    except Exception as e:
        logging.warning("An error occurred while making the markup on subreddit {}: {}".format(subreddit.display_name, e))

def insertMarkup(subreddit, markup, config, mode): # places the markup into the widget
    if "view_more_url" in config: # view more button
        markup += ("\n\n**[View more tweets]({})**".format(config['view_more_url']))
    else: # defaults
        if mode == "user": # default to profile url
            markup += ("\n\n**[View more tweets](https://www.twitter.com/{})**".format(config['screen_name']))
        elif mode == "list": # default to list url (owner username/lists/listname)
            markup += ("\n\n**[View more tweets](https://www.twitter.com/{}/lists/{})**".format(config['owner'], config['list']))
    try:
        widgets = subreddit.widgets.sidebar  # get all widgets
        for item in widgets:
            if item.shortName.lower() == 'twitterfeed': # find the feed widget
                item.mod.update(shortname="twitterfeed", text=markup) # update the widget
                logging.info("Updated the text for /r/%s" % subreddit.display_name)
                return # we're done here
    except Exception as e:
        logging.warning("An error occurred while dealing with widgets on subreddit {}: {}".format(subreddit.display_name, e))

def convertTime(t_created_at):
    d1 = t_created_at
    d2 = datetime.utcnow()
    time_diff = datetime.utcnow() - t_created_at # current time minus tweet time, both are UTC
    seconds = time_diff.total_seconds() # convert to seconds
    if seconds < 60:
        timeStr = "Just Now"
    elif 60 < seconds < 3600: # younger than 1 hour, show mins
        timeStr = str(int((seconds % 3600) // 60)) + "m"
    elif 3600 < seconds < 86400: # older than 1 hour, younger than 1 day, show hours
        timeStr = str(int(seconds // 3600)) + "h"
    else: # older than 1 day
        timeStr = t_created_at.strftime("%b %d, %Y")  # timestamp
    return timeStr.strip() # removes unwanted spaces


def tweetFormatting(t, tweet_text): # does a bunch of formatting to various parts of the tweet
    json = t._json
    linkformat = "[{}]({})"
    try: # replace links with correctly formatted text and full urls rather than t.co
        if json['entities'].get('urls') !=None:
            for i in t._json['entities']['urls']:
                fixedUrl = re.sub(r"https?://", '', i['expanded_url']).strip("/") # remove https://, http:// and trailing / so the link looks good
                tweet_text = tweet_text.replace(i['url'], linkformat.format(fixedUrl, i['expanded_url'])) # replace the t.co item with the fixedUrl (display only) and full url for the link
        if json['entities'].get('media') !=None:
            for i in t._json['entities']['media']:
                if i['type'] == 'photo': # make the image link direct to the photo
                    tweet_text = tweet_text.replace(i['url'], linkformat.format(i['display_url'], i['media_url_https'])) # replace the t.co item with the pics.twitter.com url (display only) and direct image link
                else: # links directly to the tweet/media item
                    tweet_text = tweet_text.replace(i['url'], linkformat.format(i['display_url'], i['expanded_url'])) # same as above, but links to the tweet rather than directly to content
    except Exception as e:
        logging.warning("An error occurred while formatting %s" % e)

    # find @ symbols and link to the tagged users profile
    twitterprofileUrl = "*[@{}](https://www.twitter.com/{})*"
    res = re.search('@(\w+)', tweet_text)
    if res:
        for i in res.groups():
             tweet_text = tweet_text.replace('@'+i, twitterprofileUrl.format(i, i)) # replaces with link
    return tweet_text # we are done here, return the edited tweet text


def checkCfg(subreddit, config): # False = Failed checks, True = Pass, continue code
    if 'enabled' not in config:
        sendWarning(subreddit, "Config Missing: enabled")
        return False # missing key data
    if 'mode' not in config:
        sendWarning(subreddit, "Config Missing: mode")
        return False
    if config['mode'] == 'list':
        if 'owner' not in config:
            sendWarning(subreddit, "Config Missing: Owner data is required for list mode")
            return False
        if 'list' not in config:
            sendWarning(subreddit, "Config Missing: List name is required for list mode")
            return False
        if 'users' not in config:
            sendWarning(subreddit, "Config Missing: Username's (users) are required for list mode")
            return False
    elif config['mode'] == 'user':
        if 'screen_name' not in config:
            sendWarning(subreddit, "Config Missing: Users screen name is required for user mode")
            return False
    else:
        sendWarning(subreddit, "Config Error: Mode is not set to a valid value")
        return False
    return True # if the code get's here nothing went wrong

def sendWarning(subreddit, message):
    widgets = subreddit.widgets.sidebar  # get all widgets
    for item in widgets:
        if item.shortName.lower() == 'twitterfeed':  # find the feed widget
            item.mod.update(shortname="twitterfeed", text="An error occurred with tweet_widget4 bot:\n"+message)  # update the widget
            logging.warning("An error message ({}) was posted to /r/{}".format(message, subreddit.display_name))
            return  # we're done here

def dbConnect(botconfig):
    # DB Connection
    dbName = botconfig.get("database", "dbName")
    dbPasswrd = botconfig.get("database", "dbPassword")
    dbUser = botconfig.get("database", "dbUsername")
    dbHost = botconfig.get("database", "dbHost")
    # INFO: database is setup is: subreddits(subname varchar, enabled bool DEFAULT True, latest varchar)
    try:
        global conn2
        conn2 = psycopg2.connect( # connect
            "dbname='{0}' user='{1}' host='{2}' password='{3}'".format(
                dbName, dbUser, dbHost, dbPasswrd
            )
        )
        conn2.autocommit = True
        return conn2
    except Exception as e: # could not connect
        logging.warning("Cannot connect to database")
        time.sleep(120)

def redditlogin(botconfig):
    # reddit login
    try:
        r = praw.Reddit(client_id=botconfig.get("reddit", "clientID"),
                        client_secret=botconfig.get("reddit", "clientSecret"),
                        password=botconfig.get("reddit", "password"),
                        user_agent=botconfig.get("reddit", "useragent"),
                        username=botconfig.get("reddit", "username"))
        me = r.user.me()
        return r # return reddit instance
    except Exception as e: # reddit is down
        logging.warning("Reddit/PRAW Issue, site may be down")
        time.sleep(120)

if __name__ == "__main__":
    Main()