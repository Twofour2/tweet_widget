# Generated by Django 4.0.4 on 2022-05-08 12:07

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0004_alter_subreddit_showtweetcount'),
    ]

    operations = [
        migrations.AddField(
            model_name='subreddit',
            name='widgetMembersProfileUrls',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.CharField(default='', max_length=250), default=list, size=None),
        ),
    ]