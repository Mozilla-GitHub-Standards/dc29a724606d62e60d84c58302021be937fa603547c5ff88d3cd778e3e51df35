# -*- coding: utf-8 -*-
# Generated by Django 1.11.7 on 2017-11-30 20:48
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('upload', '0014_upload_redirect_urls'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='upload',
            options={'permissions': (('upload_symbols', 'Upload Symbols Files'), ('upload_try_symbols', 'Upload Try Symbols Files'), ('view_all_uploads', 'View All Symbols Uploads'))},
        ),
    ]
