# Generated by Django 4.0.4 on 2022-05-08 13:44

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0007_subreddit_widgetprofileimagedata'),
    ]

    operations = [
        migrations.AddField(
            model_name='subreddit',
            name='nextUpdate',
            field=models.BigIntegerField(default=0),
        ),
    ]
