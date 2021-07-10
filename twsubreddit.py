import praw
import prawcore
import os
import yaml
import logging
import re
import psycopg2
import traceback
from PIL import Image
import urllib
import tweepy
from datetime import datetime, timedelta
import timeout_decorator

script_dir = os.path.split(os.path.realpath(__file__))[0]  # get where the script is
logging.basicConfig(filename=script_dir + '/logs/twitterBotSubreddits.log', level=logging.INFO,
                    format='%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')


class twSubreddit:
    global conn2  # db connection from main
    cur = None
    tApi = None

    reddit = None
    subreddit = None

    Name = "Missing"
    latestTweetID = 0
    cachedTweets = None # older tweets that are re-used when there is nothing new to show aside from the timestamps
    wikiRevisionTimestamp = 0  # used to check if the wiki page has changed
    nextUpdate = 0 # if there are no new tweets, this tells us when the next update should occur so the times on the tweets are correct
    showTweetCount = 7
    feedTitle = "Tweets"  # title displayed on the widget

    isShowingWarning = False  # if the sendWarning function is triggered
    isFailed = False  # if the bot fails to work on this subreddit, this is set
    configEnabled = False
    dbEnabled = False

    isListMode = False
    twitterID = ""  # is either username or list ID depending on the above setting

    markdown = ""  # text to be generated and placed into the widget

    bugFixImageUpload = False # when set to true, this deletes a small bit of text added to the end of the css field

    def __init__(self, subredditData, reddit):
        try:
            self.reddit = reddit

            self.Name = subredditData[0]
            self.dbEnabled = subredditData[1]
            self.latestTweetID = subredditData[2]
            self.last_gather = subredditData[3]
            self.last_update = subredditData[4]
            self.wikiRevisionTimestamp = subredditData[5]
            self.showTweetCount = subredditData[6]
            self.isListMode = subredditData[7]
            self.twitterID = subredditData[8]
            self.feedTitle = subredditData[9]

            # these two vars are used in twitter-reddit-old.py for image upload related stuff
            self.configChanged = False
            self.nextImageUploadTimestamp = datetime.utcnow().timestamp()

            # set default values
            if self.nextUpdate is None:
                self.nextUpdate = 0
            if self.latestTweetID is None:
                self.latestTweetID = 0
            if self.last_gather is None:
                self.last_gather = 0
            if self.last_update is None:
                self.last_update = 0

            # create praw subreddit object
            self.subreddit = self.reddit.subreddit(self.Name)
            try:
                self.loadConfig()
            except prawcore.ResponseException as e:
                self.logFailure(f"{self.Name}: Response exception, reddit is probably down.", exception=e)

            except timeout_decorator.TimeoutError as e:
                self.logFailure(f"{self.Name}: loadConfig() timed out {e}", exception=e)

            # get all of the users related to this widget. We use this to match the profile pictures with their tweets.
            self.widgetMembers = []
            self.loadWidgetMembers() # placed in function so we can update the values of this later

        except tweepy.error.TweepError as e:
            self.sendWarning(f"Could not find user {self.twitterID}")
        except Exception as e:
            self.logFailure(f"{self.Name} Exception during setup {e}", exception=e)

    def __str__(self):
        return self.Name

    def sendWarning(self, message):
        try:
            self.isShowingWarning = True
            endMsg = "\n\n*"
            endMsg += "[/r/Tweet_widget](https://www.reddit.com/r/tweet_widget)"
            endMsg += "*"
            message = message.replace("\n", "\n  ")
            widgets = self.subreddit.widgets.sidebar  # get all widgets
            for item in widgets:
                if item.shortName.lower() == 'twitterfeed':  # find the feed widget
                    item.mod.update(shortname="twitterfeed", text="An error occurred with tweet_widget bot:\n" + message + "\n\n" + endMsg)  # update the widget
                    logging.info("An error message ({}) was posted to /r/{}".format(message, self.Name))
                    return  # we're done here
        except Exception as e:
            logging.error(f"An error occurred while sending a warning: {e}")

    def logFailure(self, message, exception=None):
        self.isFailed = True
        logging.warning(message)
        if exception:
            print(traceback.format_exc())
            logging.error(traceback.format_exc())


    #@timeout_decorator.timeout(40)
    def loadConfig(self):
        try:
            wikiPage = self.subreddit.wiki["twittercfg"]
            if self.wikiRevisionTimestamp == wikiPage.revision_date or self.wikiRevisionTimestamp == -1:
                self.configChanged = False
                logging.info("Wiki has not been updated, using existing config")
                return  # wiki timestamps match, so it has not been updated since we last checked
            else:
                try:
                    self.configChanged = True # set a marker to inform other code that this config has been updated

                    config = yaml.load(wikiPage.content_md, Loader=yaml.FullLoader)
                    if self.checkConfig(config):
                        # now actually load the data into this subreddit class
                        self.configEnabled = config.get("enabled", True)
                        if config.get("owner", False):
                            # legacy list mode, only a few older subreddits use this as it does not work with new twitter lists
                            # to work with this, all we have to do is grab the list ID from twitter using the list name and owner name
                            logging.info(f"{self.Name}: Loaded legacy mode config")
                            self.isListMode = True
                            tList = self.tApi.get_list(owner_screen_name=config.get("owner"), slug=config.get("list").lower())
                            self.twitterID = tList.id_str
                        elif config.get("list", False):
                            # regular list ID mode
                            logging.info(f"{self.Name}: Loaded list mode config")
                            self.isListMode = True
                            self.twitterID = config.get("list")
                        elif config.get("screen_name", False):
                            # user mode
                            logging.info(f"{self.Name}: Loaded user mode config")
                            self.isListMode = False
                            self.twitterID = config.get("screen_name")
                        else:
                            self.sendWarning("Missing list ID or screen_name")

                        self.showTweetCount = config.get("count", 7)
                        if self.showTweetCount > 15:  # enforce limit
                            self.showTweetCount = 15
                        elif self.showTweetCount < 0:  # not sure if this really does anything
                            self.showTweetCount = 0

                        self.feedTitle = config.get("Title", "Tweets")

                        logging.info(f"{self.Name}: new config loaded")

                        # update the timestamp in the database
                        self.wikiRevisionTimestamp = wikiPage.revision_date
                        if self.wikiRevisionTimestamp is None:
                            self.wikiRevisionTimestamp = -1
                        self.updateDB()
                        return  # done here
                except Exception as e:
                    self.sendWarning(f"Invalid yaml in config file:\n {e}")
        except prawcore.exceptions.NotFound:
            try:
                self.subreddit.wiki.create(name='twittercfg',content='---  \nenabled: false  \nuser: Twitter  \n#list: (put list id here)')
                logging.info("Created wiki page on subreddit %s" % self.Name)
            except prawcore.exceptions.NotFound:  # occurs when lacking permissions to create wiki page
                self.logFailure(f"Tried to create wiki page but failed. Bot probably lacks permission. Subreddit: {self.Name}")
            except Exception as e:
                self.logFailure(f"{e.__class__.__name__}: Something else happened while trying to create the wiki page? This should never occur. Exception: {e}",exception=e)
        except prawcore.exceptions.ServerError as e:
            logging.error(f"{e.__class__.__name__}: HTTP error while trying to reading config from the wiki page.\nException:\n{e}")
        except Exception as e:
            logging.error(f"{e.__class__.__name__}: (Error loading config) {e}")

    def checkConfig(self, config):  # False = Failed checks, True = Pass, continue code
        if "list" not in config and "screen_name" not in config:  # if both are missing
            self.sendWarning("Missing list/user parameters in config file")
            return False
        return True  # if the code get's here nothing went wrong

    # store class variables in the database
    def updateDB(self):
        try:
            self.cur.execute(f"UPDATE subreddits_testing SET "
                             f"wiki_revision_timestamp={self.wikiRevisionTimestamp}, "
                             f"showtweetcount={self.showTweetCount}, "
                             f"listmode={self.isListMode},"
                             f"twitterID='{self.twitterID}',"
                             f"feedTitle='{self.feedTitle}'"
                             f" WHERE subname='{self.Name}'")
        except Exception as e:
            self.logFailure(f"Failed to update database {e}", exception=e)

    def updateTimestampDB(self):
        try:
            if self.latestTweetID is None:
                self.latestTweetID = 0
            if self.last_gather is None:
                self.last_gather = 0
            if self.last_update is None:
                self.last_update = 0
            if self.nextUpdate is None:
                self.nextUpdate = 0.0
            self.cur.execute(f"UPDATE subreddits_testing SET "
                             f"latest_tweet_id='{self.latestTweetID}',"
                             f"last_update='{self.last_update}',"
                             f"last_gather='{self.last_gather}',"
                             f"nextupdate='{self.nextUpdate}'"
                             f" WHERE subname='{self.Name}'")
        except Exception as e:
            self.logFailure(f"Failed to update latest timestamp {e}", exception=e)

    def loadWidgetMembers(self):
        # get all of the users related to this widget. We use this to match the profile pictures with their tweets.
        self.widgetMembers = []
        if self.isListMode:
            self.widgetMembers = self.tApi.list_members(list_id=self.twitterID)
        else:
            self.widgetMembers.append(self.tApi.get_user(screen_name=self.twitterID))

    # this function actually gets the tweets for this subreddit
    def retrieveTweets(self, count=7):
        try:
            self.last_update = datetime.utcnow().timestamp()
            if self.isListMode:
                # is a regular list ID mode list
                logging.info(f"{self.Name}: Using list ID mode")
                return self.tApi.list_timeline(list_id=self.twitterID, count=count, tweet_mode='extended',
                                               include_entities=True)
            else:  # user mode
                logging.info(f"{self.Name}: Using user mode")
                return self.tApi.user_timeline(screen_name=self.twitterID, count=count, tweet_mode='extended',
                                               include_entities=True)

        except Exception as e:
            self.logFailure(f"{self.Name}: Unable to gather tweets ({e})", exception=e)

    # main function for updating the widget for this subreddit
    #@timeout_decorator.timeout(30)
    def updateWidget(self):
        try:
            LatestTweets = self.retrieveTweets(1)
            if LatestTweets[0].id != self.latestTweetID or self.cachedTweets is None:
                logging.info(f"{self.Name}: Found a newer tweet than the stored one, using new tweets")
                FullLatestTweets = self.retrieveTweets(count=self.showTweetCount)
                self.cachedTweets = FullLatestTweets
                self.generateMarkdown(FullLatestTweets)
                self.nextUpdate = self.getTimeDiff(LatestTweets[0].created_at)
                self.updateTimestampDB()
                self.last_gather = datetime.utcnow().timestamp()
                self.latestTweetID = LatestTweets[0].id
                self.updateDB()
            else:
                if datetime.utcnow().timestamp() > self.nextUpdate or self.bugFixImageUpload:
                    logging.info(f"{self.Name}: Stored tweet id is latest, using stored tweets")
                    if self.cachedTweets is None:
                        self.logFailure(f"{self.Name}: Cached tweets are missing.")
                    self.generateMarkdown(self.cachedTweets) # generate the markdown based on the stored data
                    self.nextUpdate = self.getTimeDiff(LatestTweets[0].created_at)
                    self.updateTimestampDB()
                    self.latestTweetID = LatestTweets[0].id
                    self.updateDB()
                else:
                    logging.info(f"{self.Name}: Waiting until nextUpdate (current={datetime.utcnow().timestamp()}, next={self.nextUpdate})")
                    logging.info(f"{self.Name}: Time to wait is {datetime.fromtimestamp(self.nextUpdate) - datetime.utcnow()}")
        except Exception as e:
            self.logFailure(f"Error getting tweets {e}", exception=e)

    def getTimeDiff(self, tweet_created_at):
        time_diff = datetime.utcnow() - tweet_created_at
        seconds = time_diff.total_seconds()  # convert to seconds
        if seconds < 60:
            nextUpdate = (datetime.utcnow()+timedelta(seconds=30))
        elif 60 < seconds < 3600:  # younger than 1 hour, show mins
            nextUpdate = (datetime.utcnow()+timedelta(minutes=5))
        elif 3600 < seconds < 86400:  # older than 1 hour, younger than 1 day, show hours
            nextUpdate = (datetime.utcnow()+timedelta(hours=1, minutes=4)) # minutes=5
        else:  # older than 1 day
            nextUpdate = (datetime.utcnow()+timedelta(days=1, hours=2))
        return nextUpdate.timestamp()

    def generateMarkdown(self, Tweets):
        try:
            markdown = f"#{self.feedTitle}\n"
            userhashes = []
            if not self.isListMode:
                userhashes.append("#")
            for tweet in Tweets:
                tweet_text = Formatting.formatTweet(self, tweet)
                markdown += tweet_text
            else:
                self.uploadMarkdown(markdown)
        except KeyError as e:
            self.sendWarning(f"KeyError, check your profiles in the config! User: {e}")
        except TypeError as e:
            if tweet_text is None:
                self.logFailure(f"{self.Name}: Tweet formatting failed, tweet text is none: {e}", exception=e)
            else:
                self.logFailure(f"An error occurred while making the markup on subreddit {self.Name}: {e}", exception=e)
        except Exception as e:
            self.logFailure(f"An error occurred while making the markup on subreddit {self.Name}: {e}", exception=e)

    def uploadImages(self):
        try:
            imageDataList = [] # the post upload to reddit data goes into this

            if self.widgetMembers is None: # not sure how this happens, but it does
                if self.isListMode:
                    self.widgetMembers = self.tApi.list_members(list_id=self.twitterID)
                else:
                    self.widgetMembers.append(self.tApi.get_user(screen_name=self.twitterID))

            for image_info in ImageUploader.getProfileImages(self): # store and get the info about every profile image for this subreddits widget. Then we upload them all to reddit.
                image_url = self.subreddit.widgets.mod.upload_image(image_info.get("location"))
                imageDataList.append({"url": image_url, "width": image_info.get("width"), "height": image_info.get("height"), "name": image_info.get("name")})
            for item in self.subreddit.widgets.sidebar:
                if item.shortName.lower() == 'twitterfeed':  # find the feed widget
                    item.css = item.css.split("/* any text after this is auto-generated by the bot, any edits will be lost */", 1)[0] # removes any existing header text from the end of the file
                    item.css = item.css.split("/* MODIFY THIS FOR PROFILE PICTURES */", 1)[0] # this line only really needs to be run once on old subreddits
                    item.mod.update(imageData=[], css=item.css)  # remove any existing images, otherwise the new uploads will conflict with them
                    cssImagesText = "/* any text after this is auto-generated by the bot, any edits will be lost */" # re-add the warning text
                    for x in range(1, len(imageDataList)+1): # now generate the css headers that link them to our profile pictures we uploaded earlier
                        cssImagesText += "\nh"+str(x+1)+"::before{background-image: url(%%"+imageDataList[x-1].get("name")+"%%);} /* "+str(x)+" */"
                    for i in item.imageData:
                        if not str(i.name).startswith("profile"):
                            logging.info(f"{self.Name}: Contains a non-profile image named {i.name}. Uploading it as well")
                            imageDataList.append(i)

                    item.mod.update(imageData=imageDataList, css=item.css+cssImagesText+"/* upload image bugfix */") # upload the images along with the bugfix text that gets removed later
                    logging.info(f"{self.Name}: Uploaded new images to widget")
        except Exception as e:
            self.logFailure(f"An error occurred while uploading images: {e}", e);

    def uploadMarkdown(self, markdown):
        try:
            # insert link to list/user at bottom
            if self.isListMode:
                markdown += f"\n\n**[View more tweets](https://www.twitter.com/i/lists/{self.twitterID})**"
            else:
                markdown += f"\n\n**[View more tweets](https://www.twitter.com/{self.twitterID})**"

            # insert timestamp
            markdown += "\n\n~~"  # open code area
            markdown += f"Widget last updated: {datetime.utcnow().strftime(f'%-d %b at %-I:%M %p')} (UTC)  \n"
            if self.last_gather is not None:
                markdown += f"Last retrieved tweets: {datetime.fromtimestamp(self.last_gather).strftime(f'%-d %b at %-I:%M %p')}  (UTC)  \n"
            else:
                markdown += f"Last retrieved tweets: (Missing)  (UTC)  \n"
            markdown += "[/r/Tweet_widget](https://www.reddit.com/r/tweet_widget)"
            markdown += "~~"  # close code area

        except Exception as e:
            self.logFailure(f"An error occurred while doing end of widget text: {e}", exception=e)

        # upload to reddit
        try:
            # deletes the old subreddit objects
            # we must do this or else praw will use a cached version of "item" (the one from the uploadImages function), this causes issues as reddit will see it as being apart of the same upload
            # which in turn causes the images to not update due to a long standing bug with images.
            if self.bugFixImageUpload:
                del self.subreddit
                self.subreddit = self.reddit.subreddit(self.Name)
            widgets = self.subreddit.widgets.sidebar  # get all widgets
            for item in widgets:
                if item.shortName.lower() == 'twitterfeed':  # find the feed widget
                    if str(item.css).endswith("/* upload image bugfix */") or self.bugFixImageUpload: # redundant, just done so we are sure this actually happens
                        item.mod.update(shortname="twitterfeed", text=markdown, css=item.css.replace("/* upload image bugfix */", ""))
                        self.bugFixImageUpload = False # we no longer need to fix the images anymore
                        logging.info(f"{self.Name}: Fixed css so images show properly")
                    else:
                        item.mod.update(shortname="twitterfeed", text=markdown)
                        logging.info(f"{self.Name}: Uploaded markdown")
                    self.last_update = datetime.utcnow().timestamp()
                    self.updateTimestampDB()
                    return  # we're done here
        except Exception as e:
            self.logFailure(f"{self.Name}: An error occurred while dealing with widgets on this subreddit: {self.Name}", exception=e)


class ImageUploader:
    @staticmethod
    def getProfileImages(caller):
        try:
            allImageData = []
            caller.loadWidgetMembers() # reloads the widget members. This re-retrieves the user data in case it has been updated (i.e. new profile picture)

            for x in range(0, len(caller.widgetMembers)):
                user = caller.widgetMembers[x]
                if user.profile_image_url_https:
                    profileUrl = user.profile_image_url_https.replace("_normal", "") # _normal is removed in order to get the higher resolution version
                else:
                    caller.logFailure(f"{caller.Name} Could not find profile image url. Skipping")
                    continue  # skip this one
                allImageData.append(ImageUploader.storeImage(caller, profileUrl, x+1))  # save the image to disk, then return here with the image data
            return allImageData
        except AttributeError as e:
            caller.logFailure(f"{caller.Name}: ({e.__class__.__name__}) Failed to get widget members in getProfileImages() \n{e}", exception=e)


    @staticmethod
    # stores the image from the profileUrl to the disk. Then returns image info.
    def storeImage(caller, profileurl, profileCounter=1):
        try:
            with urllib.request.urlopen(profileurl) as image:  # open the url, to find an image
                if profileurl.endswith(".jpg"):
                    extension = ".jpg"
                elif profileurl.endswith(".png"):
                    extension = ".png"
                else:
                    extension = ".unknown"
                if not os.path.exists(rf"{script_dir}/ProfileImages/{caller.Name}"):
                    logging.info(f"Directory does not exist for {caller.Name}")
                    os.mkdir(rf"{script_dir}/ProfileImages/{caller.Name}")
                profileImage = Image.open(image)
                fileLocation = rf"{script_dir}/ProfileImages/{caller.Name}/profile{profileCounter}{extension}"
                profileImage.save(fileLocation, subsampling=0, quality=100, dpi=(400,400))
                width, height = profileImage.size
                imageInfo = {"width": width, "height": height, "location": fileLocation, "name": f"profile{profileCounter}"}
                return imageInfo
        except urllib.error.HTTPError as e:
            caller.logFailure(f"{caller.Name}: 404 on profile url {profileurl}", exception=e)
            try: # try to use the stored image instead.
                fileLocation = rf"{script_dir}/ProfileImages/{caller.Name}/profile{profileCounter}{extension}"
                with Image.open(fileLocation) as profileImage:
                    width, height = profileImage.size
                    imageInfo = {"width": width, "height": height, "location": fileLocation, "name": f"profile{profileCounter}"}
                    return imageInfo
            except Exception as e:
                caller.logFailure(f"{caller.Name}: Failed to use backup saved image \"profile{profileCounter}\"", exception=e)
        except Exception as e:
            caller.logFailure(f"{caller.Name}: Failed to store profile image \"profile{profileCounter}\"", exception=e)

class Formatting:
    hotlinkFormat = "https://www.twitter.com/{0}/status/{1}"
    # used in retweet: 0: rtweet user screen name, 1: retweet status id
    # used in normal tweet: 0: user screen name, 1: tweet id

    retweetFormat = "*🔁{0} Retweeted*\n\n>>**[{1} *@{2}*](https://www.twitter.com/{3}) *-* [*{4}*]({5})**  \n>>{6}"
    # 0: base tweet username, 1: retweet user username, 2: retweet user screenname, 3: retweet user screenname lower, 4: retweet timestamp, 5: hotlinkFormat, 6: tweet text

    tweetFormat = "\n\n---\n{0}**[{1} *@{2}*](https://www.twitter.com/{3})**   \n[*{4}*]({5}) \n>{6}"

    # 0: userhashes, 1: tweet username, 2: screenname, 3: screen name lower, 4: timestampStr, 5: hotlinkFormat, 6: tweet text

    # returns the tweet text formatting for displaying on reddit
    @staticmethod
    def formatTweet(caller, tweet):
        try:
            if hasattr(tweet, "retweeted_status"):
                tweet_text = Formatting.retweetFormat.format(tweet.user.name,
                                                             tweet.retweeted_status.user.name,
                                                             tweet.retweeted_status.user.screen_name,  # for @ link
                                                             tweet.retweeted_status.user.screen_name.lower(),  # @ link
                                                             Formatting.formatTime(tweet.retweeted_status.created_at),
                                                             # retweet timestamp text (e.x. "5 hours" or "3 days")
                                                             Formatting.hotlinkFormat.format(
                                                                 tweet.retweeted_status.user.screen_name,
                                                                 tweet.retweeted_status.id),  # link to tweet
                                                             Formatting.formatTweetLinks(caller, tweet.retweeted_status,
                                                                                         tweet.retweeted_status.full_text).replace(
                                                                 "\n", "\n>>") # takes the original tweet text format the links inside the tweet as markdown links
                                                             )
            else:
                # perform formatting on the tweet text. We specifically dont do this if the tweet is a retweet, otherwise we override the formatting/character escapes from it
                tweet_text = Formatting.formatTweetLinks(caller, tweet, tweet.full_text).replace("\n", "\n>")

            if len(tweet.user.screen_name + tweet.user.name) > 36:
                screen_name = tweet.user.screen_name[0:33]  # username is too long, shorten it
            else:
                screen_name = tweet.user.screen_name  # normal

            numUserHashes = 2
            # work out the user hashes by the order of widget members
            for x in range(0, len(caller.widgetMembers)):
                if caller.widgetMembers[x].screen_name.lower() == tweet.user.screen_name.lower():
                    numUserHashes = x+2 # offset by two, profile1 is on h2
                    break

            formatted_text = Formatting.tweetFormat.format('#' * numUserHashes, # selects which user we are showing with markdown headers
                                                           tweet.user.name,
                                                           screen_name,  # for @ link
                                                           tweet.user.screen_name.lower(),  # @ link
                                                           Formatting.formatTime(tweet.created_at),
                                                           # retweet timestamp text (e.x. "5 hours" or "3 days")
                                                           Formatting.hotlinkFormat.format(tweet.user.screen_name,
                                                                                           tweet.id),  # link to tweet
                                                           tweet_text  # insert the final text of the tweet
                                                           )
            return formatted_text
        except Exception as e:
            caller.logFailure(f"{caller.Name}: An error occurred while formatting a tweet/retweet: {e}", exception=e)

    # converts the tweet timestamp to "Just Now", "h", "m", or a date
    @staticmethod
    def formatTime(tweet_created_at):
        time_diff = datetime.utcnow() - tweet_created_at  # current time minus tweet time, both are UTC
        seconds = time_diff.total_seconds()  # convert to seconds
        if seconds < 60:
            timeStr = "Just Now"
        elif 60 < seconds < 3600:  # younger than 1 hour, show mins
            timeStr = str(int((seconds % 3600) // 60)) + "m"
        elif 3600 < seconds < 86400:  # older than 1 hour, younger than 1 day, show hours
            timeStr = str(int(seconds // 3600)) + "h"
        else:  # older than 1 day
            timeStr = tweet_created_at.strftime("%b %-d, %Y")  # timestamp
        return timeStr.strip()  # removes unwanted spaces

    # escapes markdown characters in a tweet to stop reddit from formatting on them
    @staticmethod
    def escapeChars(fulltext):
        redditChars = ["[", "]", "#", "*", ">", "^", "<", "~", "_", "`", "|", "-"]
        for i in redditChars:
            if i in fulltext:  # if i is one of the characters used by reddit for formatting
                fulltext = fulltext.replace(i, "\\" + i)  # escape the character
        else:
            return fulltext

    # method to convert links to content inside tweets to proper markdown links
    @staticmethod
    def formatTweetLinks(caller, tweet, tweet_text):
        tweet_text = Formatting.escapeChars(tweet_text)  # run the escape characters function first
        json = tweet._json
        try:  # replace links with correctly formatted text and full urls rather than t.co
            if json['entities'].get('urls') is not None:
                for i in tweet._json['entities']['urls']:
                    fixedUrl = re.sub(r"https?://", '', i['expanded_url']).strip(
                        "/")  # remove https://, http:// and trailing / so the link looks good
                    tweet_text = tweet_text.replace(i['url'],
                                                    f"[{fixedUrl}]({i['expanded_url']})")  # replace the t.co item with the fixedUrl (display only) and full url for the link
            if json['entities'].get('media') is not None:
                for i in tweet._json['entities']['media']:
                    if i.get('type') == 'photo':  # make the image link direct to the photo
                        tweet_text = tweet_text.replace(i['url'],
                                                        f"[{i['display_url']}]({i['media_url_https']})")  # replace the t.co item with the pics.twitter.com url (display only) and direct image link
                    else:  # links directly to the tweet/media item
                        tweet_text = tweet_text.replace(i['url'],
                                                        f"[{i['display_url']}]({i['expanded_url']})")  # same as above, but links to the tweet rather than directly to content
        except Exception as e:
            caller.logFailure(f"{caller.Name}: An error occurred while formatting {e}", exception=e)

        # find @ symbols and link to the tagged users profile
        res = re.findall('@(\w+)', tweet_text)
        if res:
            for i in set(res):  # using set here otherwise replace will act on duplicates multiple times
                tweet_text = tweet_text.replace('@' + i, f"*[@{i}](https://www.twitter.com/{i})*")  # replaces with link
        # find # symbols and link them
        res = re.findall("#(\w+)", tweet_text)
        if res:
            for i in set(res):  # using set here otherwise replace will act on duplicates multiple times
                tweet_text = tweet_text.replace('\#' + i,
                                                f"*[\#{i}](https://www.twitter.com/search?q=%23{i})*")  # replaces with link
        return tweet_text  # we are done here, return the edited tweet text
