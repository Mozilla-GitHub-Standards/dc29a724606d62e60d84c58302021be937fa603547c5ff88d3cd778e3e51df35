# Generated by Django 2.0.8 on 2018-08-31 13:45

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('upload', '0018_uploadscreated'),
    ]

    operations = [
        migrations.AlterField(
            model_name='uploadscreated',
            name='size',
            field=models.BigIntegerField(),
        ),
    ]
