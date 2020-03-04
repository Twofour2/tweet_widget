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
script_dir = os.path.dirname(os.path.abspath(__file__))  # get where the script is
logging.basicConfig(filename=script_dir+'/logs/twitterBot.log',level=logging.INFO, format='%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S')
#
# TWITTER WIDGET V3
# by /u/chaos_a
# a twitter feed for subreddits

def Main():
    logging.info("--------Starting Twitter Bot--------")
    botconfig = configparser.ConfigParser()
    botconfig.read(script_dir + "/botconfig.ini")
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
            if subredditdata[1]: # bot is enabled for this subreddit via database
                subreddit = reddit.subreddit(subredditdata[0]) # set the subreddit
                try:
                    wiki = subreddit.wiki['twittercfg'].content_md # get the config wiki page
                    config = yaml.load(wiki, Loader=yaml.FullLoader) # load it
                    if config: # if the file actually works
                        valid = checkCfg(subreddit, config) # validate that everything is correct
                        if valid:
                            if config.get('enabled', False): # bot is enabled via config
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
    if 'mode' in config:
        mode = config.get('mode') # get current mode
    else:
        sendWarning(subreddit, "Config Error: Missing mode type (list/user)")
        return
    count = config.get('count', 7) # get number of tweets to display
    if count > 15: # enforce limit
        count = 15
    if mode == 'user': # get tweets from a single user
        user = config.get('screen_name')
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

def genericItems(t, subreddit, config): # bunch of normally repeated code between MakeMarkupUser and MakeMarkupList
    try:
        hotlinkFormat = "https://www.twitter.com/{0}/status/{1}".format(t.user.screen_name, t.id)  # format a link to the tweet with username and tweet id
        timestampStr = convertTime(t.created_at) # tweet timestamp
        profileUrl = "https://www.twitter.com/"  # this + username gives a link to the users profile
        if hasattr(t, "retweeted_status"): # check if retweet, if so do retweet stuff
            try:
                hotlinkFormatRT = "https://www.twitter.com/{0}/status/{1}".format(t.retweeted_status.user.screen_name, t.retweeted_status.id)
                timestampStrRT = convertTime(t.retweeted_status.created_at) # get retweet timestamp
                tweet_text = tweetFormatting(t.retweeted_status, t.retweeted_status.full_text) # do tweet formatting on retweet
                tweet_text = "*🔁{} Retweeted*\n\n**[{} *@{}*]({}) *-* [*{}*]({})**  \n{}".format(t.user.name, t.retweeted_status.user.name, t.retweeted_status.user.screen_name, profileUrl+t.retweeted_status.user.screen_name.lower(), timestampStrRT, hotlinkFormatRT, tweet_text)
                fulltext = tweet_text.replace("\n","\n>>")  # double quotes so that it forms two blockquote elements
            except Exception as e:
                traceback.print_exc()
                logging.warning("An error occurred while formatting a retweet: %s" % e)
                return
        else: # isn't a retweet, just normal stuff
            tweet_text = tweetFormatting(t, t.full_text) # do tweet formatting
            fulltext = tweet_text.replace("\n","\n>")  # add the '>' character for every new line so it doesn't break the quote

        if len(t.user.screen_name + t.user.name) > 36:
            screen_name = t.user.screen_name[0:33]  # username is too long, shorten it
        else:
            screen_name = t.user.screen_name  # normal
        return hotlinkFormat, timestampStr, profileUrl, fulltext, screen_name

    except Exception as e:
        logging.warning("An error occurred while formatting a tweet/retweet: %s" % e)

def MakeMarkupUser(Tweets, subreddit, config, mode): # twitter user mode
    try:
        markup = ("#{}\n".format(config.get('title', "Tweets"))) # custom title
        for t in Tweets:
            hotlinkFormat, timestampStr, profileUrl, fulltext, screen_name = genericItems(t, subreddit, config)
            # MARKUP NOTE: 2 hashes are used here to signal %%profile1%%
            markup += ("\n\n---\n##**[{} *@{}*]({})**   \n[{}]({}) \n>{}".format(t.user.name, screen_name, profileUrl+t.user.screen_name.lower(), timestampStr, hotlinkFormat,fulltext))
            if config.get('show_retweets', False): # add re-tweet info
                markup += ("\n\n>**{}** Retweets  **{}** Likes".format(t.retweet_count, t.favorite_count))
        else: # once markup is done
            insertMarkup(subreddit, markup, config, mode) # put it on the subreddit
    except Exception as e:
        logging.warning("An error occurred while making the markup on subreddit {}: {}".format(subreddit.display_name, e))

def MakeMarkupList(Tweets, subreddit, config, mode): # twitter list mode
    global timezone
    try:
        markup = ("#{}\n".format(config.get('title', 'Tweets'))) # custom title
        userhashes = {k.casefold(): v for k, v in config['users'].items()}  # make all dict items lowercase
        for i in userhashes: # here to deal with possible user shenanigans
            if userhashes[i] > 5: userhashes[i] = 5 # any number bigger than 5, set to 5
            elif userhashes[i] <= 0: userhashes[i] = 1 # same thing, but to 1
        # FORMATTING INFO: Userhashes (above) is used to calculate which header value is used (h2-h6)
        # the rest is css magic
        for t in Tweets:
            hotlinkFormat, timestampStr, profileUrl, fulltext, screen_name = genericItems(t, subreddit, config)
            markup += ("\n\n---\n{}**[{} *@{}*]({})**   \n[{}]({}) \n>{}".format(('#'*(userhashes[t.user.screen_name.lower()]+1)), t.user.name, screen_name, profileUrl+t.user.screen_name.lower(), timestampStr, hotlinkFormat, fulltext))
            if config.get('show_retweets', False): # add re-tweet info
                markup += ("\n\n>**{}** Retweets  **{}** Likes".format(t.retweet_count, t.favorite_count))
        else: # once markup is done
            insertMarkup(subreddit, markup, config, mode) # put it on the subreddit
    except Exception as e:
        logging.warning("An error occurred while making the markup on subreddit {}: {}".format(subreddit.display_name, e))

def insertMarkup(subreddit, markup, config, mode): # places the markup into the widget
    if "view_more_url" in config: # custom view more button
        markup += ("\n\n**[View more tweets]({})**".format(config.get('view_more_url')))
    else: # default view more urls
        if mode == "user": # default to profile url
            markup += ("\n\n**[View more tweets](https://www.twitter.com/{})**".format(config.get('screen_name')))
        elif mode == "list": # default to list url (owner username/lists/listname)
            markup += ("\n\n**[View more tweets](https://www.twitter.com/{}/lists/{})**".format(config.get('owner'), config.get('list')))
    markup+= "\n\n~~Widget last updated {}".format(datetime.utcnow().strftime("%d %B %Y at %H:%I %p")+" (UTC)~~")

    if config.get('show_ad', True): # place ad into widget
        markup+= "~~[/r/Tweet_widget](https://www.reddit.com/r/tweet_widget)~~"
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

def escapeChars(fulltext): # escapes existing characters in a tweet to stop reddit from formatting on them
    redditChars = ["[", "]", "#", "*", ">", "^", "<", "~", "_", "`", "|", "-"]
    for i in redditChars:
        if i in fulltext: # if i is one of the characters used by reddit for formatting
            fulltext = fulltext.replace(i, "\\"+i) # escape the character
    else:
        return fulltext

def tweetFormatting(t, tweet_text): # does a bunch of formatting to various parts of the tweet
    tweet_text = escapeChars(tweet_text) # run the escape characters function first
    json = t._json
    linkformat = "[{}]({})"
    try: # replace links with correctly formatted text and full urls rather than t.co
        if json['entities'].get('urls') is not None:
            for i in t._json['entities']['urls']:
                fixedUrl = re.sub(r"https?://", '', i['expanded_url']).strip("/") # remove https://, http:// and trailing / so the link looks good
                tweet_text = tweet_text.replace(i['url'], linkformat.format(fixedUrl, i['expanded_url'])) # replace the t.co item with the fixedUrl (display only) and full url for the link
        if json['entities'].get('media') is not None:
            for i in t._json['entities']['media']:
                if i.get('type') == 'photo': # make the image link direct to the photo
                    tweet_text = tweet_text.replace(i['url'], linkformat.format(i['display_url'], i['media_url_https'])) # replace the t.co item with the pics.twitter.com url (display only) and direct image link
                else: # links directly to the tweet/media item
                    tweet_text = tweet_text.replace(i['url'], linkformat.format(i['display_url'], i['expanded_url'])) # same as above, but links to the tweet rather than directly to content
    except Exception as e:
        logging.warning("An error occurred while formatting %s" % e)

    # find @ symbols and link to the tagged users profile
    twitterprofileUrl = "*[@{}](https://www.twitter.com/{})*"
    res = re.findall('@(\w+)', tweet_text)
    if res:
        for i in set(res): # using set here otherwise replace will act on duplicates multiple times
             tweet_text = tweet_text.replace('@'+i, twitterprofileUrl.format(i, i)) # replaces with link
    # find # symbols and link them
    hashtagUrl = "*[\#{}](https://www.twitter.com/search?q=%23{})*"
    res = re.findall("#(\w+)", tweet_text)
    if res:
        for i in set(res): # using set here otherwise replace will act on duplicates multiple times
            tweet_text = tweet_text.replace('\#' + i, hashtagUrl.format(i, i))  # replaces with link
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