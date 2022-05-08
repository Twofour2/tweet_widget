import os

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
script_dir = os.path.split(os.path.realpath(__file__))[0]  # get where the script is

botconfig = configparser.ConfigParser()
botconfig.read(script_dir + "/botconfig.ini")


SECRET_KEY = botconfig.get("database", "secretKey")

DEFAULT_AUTO_FIELD='django.db.models.AutoField'

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgres",
        "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
    }
}

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': botconfig.get("database", "dbName"),
        'USER': botconfig.get("database", "dbUsername"),
        'PASSWORD': botconfig.get("database", "dbPassword"),
        'HOST': botconfig.get("database", "dbHost"),
        'PORT': '5432',
    }
}

INSTALLED_APPS = ("db",)
