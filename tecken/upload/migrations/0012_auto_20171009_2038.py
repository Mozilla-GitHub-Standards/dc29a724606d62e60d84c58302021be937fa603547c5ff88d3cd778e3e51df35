# -*- coding: utf-8 -*-
# Generated by Django 1.11.6 on 2017-10-09 20:38
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('upload', '0011_auto_20170912_1823'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='upload',
            name='attempts',
        ),
        migrations.RemoveField(
            model_name='upload',
            name='cancelled_at',
        ),
        migrations.RemoveField(
            model_name='upload',
            name='inbox_filepath',
        ),
        migrations.RemoveField(
            model_name='upload',
            name='inbox_key',
        ),
    ]
