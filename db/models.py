import sys
import logging
import os
from django.forms import JSONField
import praw
import prawcore
import yaml
import logging
import re
import tweepy
import urllib
import psycopg2
import traceback
from PIL import Image
from datetime import datetime, tzinfo, timedelta
from django.contrib.postgres.fields import ArrayField

from django.db import models

script_dir = os.path.split(os.path.realpath(__file__))[0]  # get where the script is
logging.basicConfig(filename=script_dir + '/../logs/twitterBot.log', level=logging.INFO,
                    format='%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')

class Subreddit(models.Model):
    subname = models.CharField(max_length=50, default="")
    enabled = models.BooleanField(default=False)
    latestTweetId = models.CharField(max_length=50, default="")
    wikiTimestamp = models.BigIntegerField(default=0)
    showTweetCount = models.IntegerField(default=7)
    isListMode = models.BooleanField(default=False)
    twitterId = models.CharField(max_length=50, default="Twitter")
    feedTitle = models.CharField(max_length=50, default="Tweets")
    lastUpdate = models.BigIntegerField(default=0)
    nextUpdate = models.BigIntegerField(default=0)
    widgetMembers = ArrayField(models.CharField(max_length=50, default="Twitter"), default=list)
    widgetProfileUrls = ArrayField(models.CharField(max_length=250, default=""), default=list)
    widgetProfileImageData = models.JSONField(default=list)
    nextImageUploadTimestamp = models.BigIntegerField(default=0)
    bugFixImageUpload = models.BooleanField(default=False)
    showReweets = models.BooleanField(default=True)
    showReplies = models.BooleanField(default=True)

    subreddit = None
    r = None
    tApi = None
    tweets = []
    configChanged = False
    widgetProfilesHasChanged = False

    def __str(self):
        return self.subname

    def loadConfig(self, isFirstLoad):
        self.subreddit = self.r.subreddit(self.subname)
        try:
            wikiPage = self.subreddit.wiki["twittercfg"]
            if self.wikiTimestamp == wikiPage.revision_date and not isFirstLoad:
                self.configChanged = False
                logging.info("Wiki has not been updated, using saved config")
                return
            else:
                try:
                    self.configChanged = True
                    config = yaml.load(wikiPage.content_md, Loader=yaml.FullLoader)
                    if "list" not in config and "screen_name" not in config:  # if both are missing
                        self.sendWarning("Missing list/user parameters in config file")
                        return

                    if config.get("owner", False): # runs first since the keyword list is used by it
                        # legacy list mode, only a few older subreddits use this as it does not work with new twitter lists
                        # to work with this, all we have to do is grab the list ID from twitter using the list name and owner name
                        logging.info(f"{self.subname}: Loaded legacy mode config")
                        self.isListMode = True
                        tList = self.tApi.get_list(owner_screen_name=config.get("owner"), slug=config.get("list").lower())
                        self.twitterId = tList.id_str
                    elif config.get("list", False):
                        logging.info(f"{self.subname}: Loaded list mode config")
                        self.isListMode = True
                        self.twitterId = config.get("list")
                    elif config.get("screen_name", False):
                        # user mode
                        logging.info(f"{self.subname}: Loaded user mode config")
                        self.isListMode = False
                        self.twitterId = config.get("screen_name")
                    else:
                        self.sendWarning("Missing list ID or screen_name")
                    
                    self.showTweetCount = config.get("count", 7)
                    if self.showTweetCount > 15:  # enforce limit
                        self.showTweetCount = 15
                    elif self.showTweetCount < 0:  # not sure if this really does anything
                        self.showTweetCount = 0

                    self.feedTitle = config.get("title", False)
                    if self.feedTitle == False: # for a while, this rarely used feature was looking for a capital
                        self.feedTitle = config.get("Title", "Tweets")
                    
                    self.showReweets = config.get("show_retweets", True)
                    self.showReplies = config.get("show_replies", True)

                    self.loadWidgetMembers() # get all of the users related to this widget. We use this to match the profile pictures with their tweets.
                    self.wikiTimestamp = wikiPage.revision_date
                    self.save()

                    logging.info(f"{self.subname}: config loaded")
                    return
                except Exception as e:
                    self.sendWarning(f"Invalid yaml in config file:\n {e}")
        except prawcore.exceptions.NotFound:
            try:
                self.subreddit.wiki.create(name='twittercfg',content='---  \nenabled: false  \nuser: Twitter  \n#list: (put list id here)')
                logging.info("Created wiki page on subreddit %s" % self.subame)
            except prawcore.exceptions.NotFound:  # occurs when lacking permissions to create wiki page
                self.logFailure(f"Tried to create wiki page but failed. Bot probably lacks permission. Subreddit: {self.subname}")
            except prawcore.exceptions.ServerError as e:
                logging.error(f"{self.subname}: HTTP error while trying to reading config from the wiki page.\nException:\n{e}")
            except Exception as e:
                self.logFailure(f"{self.subname}: Something else happened while trying to create the wiki page? This should never occur. Exception: {e}",exception=e)
            
    
    def getTweets(self, count=7):
        try:
            self.lastUpdate = datetime.utcnow().timestamp()
            if self.isListMode:
                # is a regular list ID mode list
                logging.info(f"{self.subname}: Using list ID mode")
                return self.tApi.list_timeline(list_id=self.twitterId, count=count, tweet_mode='extended',
                                               include_entities=True, include_rts=self.showReweets)
            else:  # user mode
                logging.info(f"{self.subname}: Using user mode")
                return self.tApi.user_timeline(screen_name=self.twitterId, count=count, tweet_mode='extended', include_rts=self.showReweets, exclude_replies= (not self.showReplies))

        except Exception as e:
            self.logFailure(f"{self.subname}: Unable to gather tweets ({e})", exception=e)

    def updateWidget(self):
        try:
            self.tweets = self.getTweets(1)
            if self.tweets[0].id != self.latestTweetId or self.tweets is None:
                logging.info(f"{self.subname}: Found a newer tweet than the stored one, using new tweets")
                self.tweets = self.getTweets(count=self.showTweetCount) # get all of the tweets
                self.generateMarkdown()
                self.lastUpdate = datetime.utcnow().timestamp()
                self.nextUpdate = self.getTimeDiff(self.tweets[0].created_at)
                self.latestTweetId = self.tweets[0].id
                self.save()
            elif datetime.utcnow().timestamp() > self.nextUpdate or self.bugFixImageUpload:
                logging.info(f"{self.Name}: Stored tweet id is latest, using stored tweets")
                if self.cachedTweets is None:
                    self.logFailure(f"{self.Name}: Cached tweets are missing.")
                self.generateMarkdown(self.cachedTweets) # generate the markdown based on the stored data
                self.nextUpdate = self.getTimeDiff(self.tweets[0].created_at)
                self.latestTweetID = self.tweets[0].id
            else:
                logging.info(f"{self.subname}: Waiting until nextUpdate (current={datetime.utcnow().timestamp()}, next={self.nextUpdate})")
                logging.info(f"{self.subname}: Time to wait is {datetime.fromtimestamp(self.nextUpdate) - datetime.utcnow()}")

        except Exception as e:
            self.logFailure(f"{self.subname}: Error getting tweets {e}", exception=e)

    def generateMarkdown(self):
        try:
            markdown = f"#{self.feedTitle}\n"
            for tweet in self.tweets:
                markdown += self.generateTweetMd(tweet)
            else:
                self.uploadMarkdown(markdown)
        except KeyError as e:
            self.sendWarning(f"KeyError, check your profiles in the config! User: {e}")
        except TypeError as e:
            if markdown is None:
                self.logFailure(f"{self.subname}: Tweet formatting failed, tweet text is none: {e}", exception=e)
            else:
                self.logFailure(f"An error occurred while making the markup on subreddit {self.subname}: {e}", exception=e)
        except Exception as e:
            self.logFailure(f"An error occurred while making the markup on subreddit {self.subname}: {e}", exception=e)

    # returns the tweet text formatting for displaying on reddit
    def generateTweetMd(self, tweet):
        try:
            if getattr(tweet, "retweeted_status", False):
                baseuser = getattr(tweet, "user", None)
                retweet_user = getattr(tweet.retweeted_status, "user", None)
                if baseuser and retweet_user:
                    hotlink = f"https://www.twitter.com/{tweet.retweeted_status.user.screen_name}/status/{tweet.retweeted_status.id}"
                    inner_tweet_text = self.formatTweetLinks(tweet.retweeted_status, tweet.retweeted_status.full_text).replace("\n", "\n>>")
                    tweet_text = f"*🔁{baseuser.name} Retweeted*\n\n>>**[{retweet_user.name} *@{retweet_user.screen_name}*](https://www.twitter.com/{retweet_user.screen_name.lower()}) *-* [*{self.formatTime(tweet.retweeted_status.created_at)}*]({hotlink})**  \n>>{inner_tweet_text}"
            else:
                tweet_text = self.formatTweetLinks(tweet, tweet.full_text).replace("\n", "\n>")
            
            screen_name = tweet.user.screen_name  # normal
            if len(tweet.user.screen_name + tweet.user.name) > 36:
                screen_name = tweet.user.screen_name[0:33]  # username is too long, shorten it
            
            userhashes = "#" * (self.widgetMembers.index(tweet.user.screen_name.lower()) + 2)
            hotlink = f"https://www.twitter.com/{tweet.user.screen_name}/status/{tweet.id}"
            tweet_text = f"\n\n---\n{userhashes}**[{tweet.user.name} *@{screen_name}*](https://www.twitter.com/{tweet.user.screen_name.lower()})**   \n[*{self.formatTime(tweet.created_at)}*]({hotlink}) \n>{tweet_text}"
            return tweet_text
        except Exception as e:
            self.logFailure(f"{self.subname}: An error occurred while formatting a tweet/retweet: {e}", exception=e)

    def uploadMarkdown(self, markdown):
        try:
            # insert link to list/user at bottom
            if self.isListMode:
                markdown += f"\n\n**[View more tweets](https://www.twitter.com/i/lists/{self.twitterId})**"
            else:
                markdown += f"\n\n**[View more tweets](https://www.twitter.com/{self.twitterId})**"
            
            # insert timestamp
            markdown += "\n\n~~"  # open code area
            markdown += f"Widget last updated: {datetime.utcnow().strftime(f'%-d %b at %-I:%M %p')} (UTC)  \n"
            if self.lastUpdate is not None:
                markdown += f"Last retrieved tweets: {datetime.fromtimestamp(self.lastUpdate).strftime(f'%-d %b at %-I:%M %p')}  (UTC)  \n"
            else:
                markdown += f"Last retrieved tweets: (Missing)  (UTC)  \n"
            markdown += "[/r/Tweet_widget](https://www.reddit.com/r/tweet_widget)"
            markdown += "~~"  # close code area

        except Exception as e:
            self.logFailure(f"An error occurred while doing end of widget text: {e}", exception=e)

        try:
            # deletes the old subreddit objects
            # we must do this or else praw will use a cached version of "item" (the one from the uploadImages function), this causes issues as reddit will see it as being apart of the same upload
            # which in turn causes the images to not update due to a long standing bug with images.
            if self.bugFixImageUpload:
                del self.subreddit
                self.subreddit = self.r.subreddit(self.subname)
            widgets = self.subreddit.widgets.sidebar  # get all widgets
            for item in widgets:
                if item.shortName.lower() == 'twitterfeed':  # find the feed widget
                    if str(item.css).endswith("/* upload image bugfix */") or self.bugFixImageUpload: # redundant, just done so we are sure this actually happens
                        for image in item.imageData:
                            if image.url == 'https://www.redditstatic.com/image-processing.png':
                                logging.info(f"Images are still processing. Waiting until next cycle.")
                                self.bugFixImageUpload = True
                                break
                        else:
                            item.mod.update(shortname="twitterfeed", text=markdown, css=item.css.replace("/* upload image bugfix */", ""))
                            self.bugFixImageUpload = False # we no longer need to fix the images anymore
                            logging.info(f"{self.subname}: Fixed css so images show properly")
                    else:
                        item.mod.update(shortname="twitterfeed", text=markdown)
                        logging.info(f"{self.subname}: Uploaded markdown")
                    return  # we're done here
        except Exception as e:
            self.logFailure(f"{self.subname}: An error occurred while dealing with widgets on this subreddit: {self.subname}", exception=e)


    def uploadImages(self):
        try:
            allImageData = []
            count = 0
            if self.widgetProfilesHasChanged or len(self.widgetProfileImageData) == 0 or self.configChanged:
                for url in self.widgetProfileUrls:
                    # check if url has changed
                    allImageData.append(self.storeImage(url, count))  # save the image to disk, then return here with the image data
                    count += 1
                self.widgetProfileImageData = allImageData
                self.save()
        except Exception as e:
            self.logFailure(f"An error occurred while uploading images: {e}", e)

        imageDataList = []
        for image_info in self.widgetProfileImageData:
            image_url = self.subreddit.widgets.mod.upload_image(image_info.get("location"))
            imageDataList.append({"url": image_url, "width": image_info.get("width"), "height": image_info.get("height"), "name": image_info.get("name")})
        for item in self.subreddit.widgets.sidebar:
            if item.shortName.lower() == 'twitterfeed':  # find the feed widget
                # we need to make sure that any non-profile related images remain, otherwise reddit wont let us upload. So we store another list with their image data.
                otherImageData = []
                for i in item.imageData:
                    if not str(i.name).startswith("profile"):
                        logging.info(f"{self.subname}: Contains a non-profile image named {i.name}. Uploading it as well")
                        otherImageData.append(i)

                item.css = item.css.split("/* any text after this is auto-generated by the bot, any edits will be lost */", 1)[0] # removes any existing header text from the end of the file
                item.css = item.css.split("/* MODIFY THIS FOR PROFILE PICTURES */", 1)[0] # this line only really needs to be run once on old subreddits

                item.mod.update(imageData=otherImageData[:5], css=item.css)  # remove any existing profile images, otherwise the new uploads will conflict with them

                cssImagesText = "/* any text after this is auto-generated by the bot, any edits will be lost */" # re-add the warning text
                for x in range(1, len(imageDataList)+1): # now generate the css headers that link them to our profile pictures we uploaded earlier
                    cssImagesText += "\nh"+str(x+1)+"::before{background-image: url(%%"+imageDataList[x-1].get("name")+"%%);} /* "+str(x)+" */"
                logging.info(f"{self.subname}: Removed existing images from the widget.")

                item.mod.update(imageData=(otherImageData + imageDataList)[:5], css=item.css + cssImagesText + "/* upload image bugfix */")  # upload the images along with the bugfix text that gets removed later
                logging.info(f"{self.subname}: Uploaded new images to widget") 
                self.bugFixImageUpload = True
                self.save()

    def storeImage(self, profileUrl, profileCounter=1):
        try:
            with urllib.request.urlopen(profileUrl) as image:  # open the url, to find an image
                if profileUrl.endswith(".jpg") or profileUrl.endswith(".jpeg"):
                    extension = ".jpg"
                elif profileUrl.endswith(".png"):
                    extension = ".png"
                else:
                    extension = ".unknown"

                if not os.path.exists(rf"{script_dir}/../ProfileImages/{self.subname}"):
                    logging.info(f"Directory does not exist for {self.subname}")
                    os.mkdir(rf"{script_dir}/../ProfileImages/{self.subname}")
                profileImage = Image.open(image)
                fileLocation = os.path.join("ProfileImages", self.subname, f"profile{profileCounter}{extension}")
                profileImage.save(fileLocation, subsampling=0, quality=100, dpi=(73, 73))
                width, height = profileImage.size
                imageInfo = {"width": width, "height": height, "location": fileLocation, "name": f"profile{profileCounter}"}
                return imageInfo
        except urllib.error.HTTPError as e:
            self.logFailure(f"{self.subname}: 404 on profile url {profileUrl}", exception=e)
            try: # try to use the stored image instead.
                fileLocation = os.path.join("ProfileImages", self.subname, f"profile{profileCounter}{extension}")
                with Image.open(fileLocation) as profileImage:
                    width, height = profileImage.size
                    imageInfo = {"width": width, "height": height, "location": fileLocation, "name": f"profile{profileCounter}"}
                    return imageInfo
            except Exception as e:
                self.logFailure(f"{self.subname}: Failed to use backup saved image \"profile{profileCounter}\"", exception=e)
        except Exception as e:
            self.logFailure(f"{self.subname}: Failed to store profile image \"profile{profileCounter}\"", exception=e)

     # used to get how long we should force an update depending on how old the tweets are
    def getTimeDiff(self, tweet_created_at):
        time_diff = datetime.utcnow() - tweet_created_at.replace(tzinfo=None)
        seconds = time_diff.total_seconds()  # convert to seconds
        if seconds < 60:
            nextUpdate = (datetime.utcnow()+timedelta(seconds=30))
        elif 60 < seconds < 3600:  # younger than 1 hour
            nextUpdate = (datetime.utcnow()+timedelta(minutes=5))
        elif 3600 < seconds < 86400:  # older than 1 hour, younger than 1 day
            nextUpdate = (datetime.utcnow()+timedelta(hours=1, minutes=4)) # minutes=5
        else:  # older than 1 day
            nextUpdate = (datetime.utcnow()+timedelta(days=1, hours=2))
        return nextUpdate.timestamp()

    def formatTime(self, tweet_created_at):
        time_diff = datetime.utcnow() - tweet_created_at.replace(tzinfo=None)  # current time minus tweet time, both are UTC
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
    
    def escapeChars(self, fulltext):
        redditChars = ["[", "]", "#", "*", ">", "^", "<", "~", "_", "`", "|", "-"]
        for i in redditChars:
            if i in fulltext:  # if i is one of the characters used by reddit for formatting
                fulltext = fulltext.replace(i, "\\" + i)  # escape the character
        else:
            return fulltext

    # method to convert links to content inside tweets to proper markdown links
    def formatTweetLinks(self, tweet, tweet_text):
        tweet_text = self.escapeChars(tweet_text)  # run the escape characters function first
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
            self.logFailure(f"{self.subname}: An error occurred while formatting {e}", exception=e)

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
    
    def loadWidgetMembers(self):
        # get all of the users related to this widget. We use this to match the profile pictures with their tweets.
        try:
            if self.isListMode:
                users = self.tApi.get_list_members(list_id=self.twitterId)
                self.widgetMembers = list(map(lambda user: user.screen_name.lower(), users))
                widgetProfiles = list(map(lambda user: user.profile_image_url_https.replace("_normal", "_bigger"), users))
            else:
                self.widgetMembers = [self.twitterId.lower()]
                user = self.tApi.get_user(screen_name=self.twitterId)
                widgetProfiles = [user.profile_image_url_https.replace("_normal", "_bigger")]
            if self.widgetProfileUrls != widgetProfiles:
                self.widgetProfilesHasChanged = True
                self.widgetProfileUrls = widgetProfiles
            else:
                self.widgetProfilesHasChanged = False
        except tweepy.errors.TweepyException:
            self.sendWarning(f"User or list owner does not exist: {self.twitterId}")
            return
    

    def sendWarning(self, message):
        try:
            self.isShowingWarning = True
            endMsg = f"\n\n*[/r/Tweet_widget](https://www.reddit.com/r/tweet_widget)*"
            message = message.replace("\n", "\n  ")
            widgets = self.subreddit.widgets.sidebar  # get all widgets
            for item in widgets:
                if item.shortName.lower() == 'twitterfeed':  # find the feed widget
                    item.mod.update(shortname="twitterfeed", text=f"An error occurred with tweet_widget bot:\n{message}\n\n{endMsg}")  # update the widget
                    logging.info(f"An error message ({message}) was posted to /r/{self.subname}")
                    return  # we're done here
        except Exception as e:
            logging.error(f"An error occurred while sending a warning: {e}")


    def logFailure(self, message, exception=None):
        print(message)
        logging.warning(message)
        if exception:
            print(traceback.format_exc())
            logging.error(traceback.format_exc())