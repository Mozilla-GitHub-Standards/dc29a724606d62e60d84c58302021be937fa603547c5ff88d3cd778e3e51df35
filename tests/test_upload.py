# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.

import gzip
import os
from io import BytesIO

import pytest
from botocore.exceptions import ClientError
from requests.exceptions import ConnectionError
from markus import TIMING, INCR

from django.core.urlresolvers import reverse
from django.contrib.auth.models import Permission
from django.core.exceptions import ImproperlyConfigured

from tecken.tokens.models import Token
from tecken.upload.models import Upload, FileUpload
from tecken.upload import utils
# from tecken.upload.tasks import upload_inbox_upload
from tecken.base.symboldownloader import SymbolDownloader
# from tecken.boto_extra import OwnEndpointConnectionError, OwnClientError
from tecken.upload.views import get_bucket_info
from tecken.upload.forms import UploadByDownloadForm
from tecken.upload.utils import get_archive_members


_here = os.path.dirname(__file__)
ZIP_FILE = os.path.join(_here, 'sample.zip')
TGZ_FILE = os.path.join(_here, 'sample.tgz')
TARGZ_FILE = os.path.join(_here, 'sample.tar.gz')
INVALID_ZIP_FILE = os.path.join(_here, 'invalid.zip')
ACTUALLY_NOT_ZIP_FILE = os.path.join(_here, 'notazipdespiteitsname.zip')


def test_get_archive_members():
    with open(TGZ_FILE, 'rb') as f:
        file_listing, = get_archive_members(f, f.name)
        assert file_listing.name == (
            'south-africa-flag/deadbeef/south-africa-flag.jpeg'
        )
        assert file_listing.size == 69183

    with open(TARGZ_FILE, 'rb') as f:
        file_listing, = get_archive_members(f, f.name)
        assert file_listing.name == (
            'south-africa-flag/deadbeef/south-africa-flag.jpeg'
        )
        assert file_listing.size == 69183

    with open(ZIP_FILE, 'rb') as f:
        file_listings = list(get_archive_members(f, f.name))
        # That .zip file has multiple files in it so it's hard to rely
        # on the order.
        assert len(file_listings) == 3
        for file_listing in file_listings:
            assert file_listing.name
            assert file_listing.size


@pytest.mark.django_db
def test_upload_archive_happy_path(client, botomock, fakeuser, metricsmock):

    token = Token.objects.create(user=fakeuser)
    permission, = Permission.objects.filter(codename='upload_symbols')
    token.permissions.add(permission)
    url = reverse('upload:upload_archive')

    def mock_api_call(self, operation_name, api_params):
        # This comes for the setting UPLOAD_DEFAULT_URL specifically
        # for tests.
        assert api_params['Bucket'] == 'private'
        if operation_name == 'HeadBucket':
            # yep, bucket exists
            return {}

        if (
            operation_name == 'ListObjectsV2' and
            api_params['Prefix'] == (
                'v0/south-africa-flag/deadbeef/south-africa-flag.jpeg'
            )
        ):
            # Pretend that we have this in S3 and its previous
            # size was 1000.
            return {'Contents': [
                {
                    'Key': (
                        'v0/south-africa-flag/deadbeef/south-africa-flag.jpeg'
                    ),
                    'Size': 1000,
                }
            ]}

        if (
            operation_name == 'ListObjectsV2' and
            api_params['Prefix'] == (
                'v0/xpcshell.dbg/A7D6F1BB18CD4CB48/xpcshell.sym'
            )
        ):
            # Pretend we don't have this in S3 at all
            return {}

        if (
            operation_name == 'PutObject' and
            api_params['Key'] == (
                'v0/south-africa-flag/deadbeef/south-africa-flag.jpeg'
            )
        ):
            assert 'ContentEncoding' not in api_params
            assert 'ContentType' not in api_params
            content = api_params['Body'].read()
            assert isinstance(content, bytes)
            # based on `unzip -l tests/sample.zip` knowledge
            assert len(content) == 69183

            # ...pretend to actually upload it.
            return {
                # Should there be anything here?
            }
        if (
            operation_name == 'PutObject' and
            api_params['Key'] == (
                'v0/xpcshell.dbg/A7D6F1BB18CD4CB48/xpcshell.sym'
            )
        ):
            # Because .sym is in settings.COMPRESS_EXTENSIONS
            assert api_params['ContentEncoding'] == 'gzip'
            # Because .sym is in settings.MIME_OVERRIDES
            assert api_params['ContentType'] == 'text/plain'
            body = api_params['Body'].read()
            assert isinstance(body, bytes)
            # If you look at the fixture 'sample.zip', which is used in
            # these tests you'll see that the file 'xpcshell.sym' is
            # 1156 originally. But we asser that it's now *less* because
            # it should have been gzipped.
            assert len(body) < 1156
            original_content = gzip.decompress(body)
            assert len(original_content) == 1156

            # ...pretend to actually upload it.
            return {}

        raise NotImplementedError((operation_name, api_params))

    with botomock(mock_api_call), open(ZIP_FILE, 'rb') as f:
        response = client.post(
            url,
            {'file.zip': f},
            HTTP_AUTH_TOKEN=token.key,
        )
        assert response.status_code == 201

        upload, = Upload.objects.all()
        assert upload.user == fakeuser
        # assert upload.inbox_key is None
        # assert expected_inbox_key_name_regex.findall(upload.inbox_filepath)
        assert upload.filename == 'file.zip'
        assert upload.completed_at
        # based on `ls -l tests/sample.zip` knowledge
        assert upload.size == 69812
        assert upload.bucket_name == 'private'
        assert upload.bucket_region is None
        assert upload.bucket_endpoint_url == 'https://s3.example.com'
        assert upload.skipped_keys is None
        assert upload.ignored_keys == ['build-symbols.txt']

    assert FileUpload.objects.all().count() == 2
    file_upload = FileUpload.objects.get(
        upload=upload,
        bucket_name='private',
        key='v0/south-africa-flag/deadbeef/south-africa-flag.jpeg',
        compressed=False,
        update=True,
        size=69183,  # based on `unzip -l tests/sample.zip` knowledge
    )
    assert file_upload.completed_at

    file_upload = FileUpload.objects.get(
        upload=upload,
        bucket_name='private',
        key='v0/xpcshell.dbg/A7D6F1BB18CD4CB48/xpcshell.sym',
        compressed=True,
        update=False,
        # Based on `unzip -l tests/sample.zip` knowledge, but note that
        # it's been compressed.
        size__lt=1156,
        completed_at__isnull=False,
    )

    # Check that markus caught timings of the individual file processing
    records = metricsmock.get_records()
    assert len(records) == 5
    assert records[0][0] == TIMING
    assert records[1][0] == INCR
    assert records[2][0] == TIMING
    assert records[3][0] == INCR
    assert records[4][0] == TIMING


@pytest.mark.django_db
def test_upload_archive_one_uploaded_one_skipped(
    client,
    botomock,
    fakeuser,
    metricsmock
):

    token = Token.objects.create(user=fakeuser)
    permission, = Permission.objects.filter(codename='upload_symbols')
    token.permissions.add(permission)
    url = reverse('upload:upload_archive')

    def mock_api_call(self, operation_name, api_params):
        # This comes for the setting UPLOAD_DEFAULT_URL specifically
        # for tests.
        assert api_params['Bucket'] == 'private'
        if operation_name == 'HeadBucket':
            # yep, bucket exists
            return {}

        if (
            operation_name == 'ListObjectsV2' and
            api_params['Prefix'] == (
                'v0/south-africa-flag/deadbeef/south-africa-flag.jpeg'
            )
        ):
            return {'Contents': [
                {
                    'Key': (
                        'v0/south-africa-flag/deadbeef/south-africa-flag.jpeg'
                    ),
                    # based on `unzip -l tests/sample.zip` knowledge
                    'Size': 69183,
                }
            ]}

        if (
            operation_name == 'ListObjectsV2' and
            api_params['Prefix'] == (
                'v0/xpcshell.dbg/A7D6F1BB18CD4CB48/xpcshell.sym'
            )
        ):
            # Not found at all
            return {}

        if (
            operation_name == 'PutObject' and
            api_params['Key'] == (
                'v0/xpcshell.dbg/A7D6F1BB18CD4CB48/xpcshell.sym'
            )
        ):
            # ...pretend to actually upload it.
            return {}

        raise NotImplementedError((operation_name, api_params))

    with botomock(mock_api_call), open(ZIP_FILE, 'rb') as f:
        response = client.post(
            url,
            {'file.zip': f},
            HTTP_AUTH_TOKEN=token.key,
        )
        assert response.status_code == 201

        upload, = Upload.objects.all()
        assert upload.user == fakeuser
        # assert upload.inbox_key is None
        # assert expected_inbox_key_name_regex.findall(upload.inbox_filepath)
        assert upload.filename == 'file.zip'
        assert upload.completed_at
        # based on `ls -l tests/sample.zip` knowledge
        assert upload.size == 69812
        assert upload.bucket_name == 'private'
        assert upload.bucket_region is None
        assert upload.bucket_endpoint_url == 'https://s3.example.com'
        assert upload.skipped_keys == [
            'v0/south-africa-flag/deadbeef/south-africa-flag.jpeg'
        ]
        assert upload.ignored_keys == ['build-symbols.txt']

    assert FileUpload.objects.all().count() == 1
    assert FileUpload.objects.get(
        upload=upload,
        bucket_name='private',
        key='v0/xpcshell.dbg/A7D6F1BB18CD4CB48/xpcshell.sym',
        compressed=True,
        update=False,
        # Based on `unzip -l tests/sample.zip` knowledge, but note that
        # it's been compressed.
        size__lt=1156,
        completed_at__isnull=False,
    )


@pytest.mark.django_db
def test_upload_archive_with_cache_invalidation(
    client,
    botomock,
    fakeuser,
    metricsmock,
    settings
):

    settings.SYMBOL_URLS = ['https://s3.example.com/mybucket']
    settings.UPLOAD_DEFAULT_URL = 'https://s3.example.com/mybucket'
    downloader = SymbolDownloader(settings.SYMBOL_URLS)
    utils.downloader = downloader

    token = Token.objects.create(user=fakeuser)
    permission, = Permission.objects.filter(codename='upload_symbols')
    token.permissions.add(permission)
    url = reverse('upload:upload_archive')

    # A mutable we use to help us distinguish between calls in the mock
    lookups = []

    def mock_api_call(self, operation_name, api_params):
        # This comes for the setting UPLOAD_DEFAULT_URL specifically
        # for tests.
        print(operation_name, api_params)
        assert api_params['Bucket'] == 'mybucket'
        if operation_name == 'HeadBucket':
            # yep, bucket exists
            return {}

        if (
            operation_name == 'ListObjectsV2' and
            api_params['Prefix'] == (
                'v0/south-africa-flag/deadbeef/south-africa-flag.jpeg'
            )
        ):
            # Pretend that we have this in S3 and its previous
            # size was 1000.
            return {'Contents': [
                {
                    'Key': (
                        'v0/south-africa-flag/deadbeef/south-africa-flag.jpeg'
                    ),
                    'Size': 1000,
                }
            ]}

        if (
            operation_name == 'ListObjectsV2' and
            api_params['Prefix'] == (
                'v0/xpcshell.dbg/A7D6F1BB18CD4CB48/xpcshell.sym'
            )
        ):
            if not lookups:
                # This is when the SymbolDownloader queries it.
                result = {}
            elif len(lookups) == 1:
                # This is when the upload task queries it.
                result = {}
            else:
                result = {
                    'Contents': [
                        {
                            'Key': api_params['Prefix'],
                            'Size': 100,
                        }
                    ]
                }
            lookups.append(api_params['Prefix'])
            # Pretend we don't have this in S3 at all
            return result

        if (
            operation_name == 'PutObject' and
            api_params['Key'] == (
                'v0/south-africa-flag/deadbeef/south-africa-flag.jpeg'
            )
        ):
            # ...pretend to actually upload it.
            return {
                # Should there be anything here?
            }
        if (
            operation_name == 'PutObject' and
            api_params['Key'] == (
                'v0/xpcshell.dbg/A7D6F1BB18CD4CB48/xpcshell.sym'
            )
        ):
            # ...pretend to actually upload it.
            return {}

        raise NotImplementedError((operation_name, api_params))

    with botomock(mock_api_call), open(ZIP_FILE, 'rb') as f:

        assert not downloader.has_symbol(
            'xpcshell.dbg', 'A7D6F1BB18CD4CB48', 'xpcshell.sym'
        )

        response = client.post(
            url,
            {'file.zip': f},
            HTTP_AUTH_TOKEN=token.key,
        )
        assert response.status_code == 201

        # Second time.
        assert downloader.has_symbol(
            'xpcshell.dbg', 'A7D6F1BB18CD4CB48', 'xpcshell.sym'
        )

        # This is just basically to make sense of all the crazy mocking.
        assert len(lookups) == 3


@pytest.mark.django_db
def test_upload_archive_both_skipped(
    client,
    botomock,
    fakeuser,
    metricsmock
):

    token = Token.objects.create(user=fakeuser)
    permission, = Permission.objects.filter(codename='upload_symbols')
    token.permissions.add(permission)
    url = reverse('upload:upload_archive')

    def mock_api_call(self, operation_name, api_params):
        # This comes for the setting UPLOAD_DEFAULT_URL specifically
        # for tests.
        assert api_params['Bucket'] == 'private'
        if operation_name == 'HeadBucket':
            # yep, bucket exists
            return {}

        if (
            operation_name == 'ListObjectsV2' and
            api_params['Prefix'] == (
                'v0/south-africa-flag/deadbeef/south-africa-flag.jpeg'
            )
        ):
            return {'Contents': [
                {
                    'Key': (
                        'v0/south-africa-flag/deadbeef/south-africa-flag.jpeg'
                    ),
                    # based on `unzip -l tests/sample.zip` knowledge
                    'Size': 69183,
                }
            ]}

        if (
            operation_name == 'ListObjectsV2' and
            api_params['Prefix'] == (
                'v0/xpcshell.dbg/A7D6F1BB18CD4CB48/xpcshell.sym'
            )
        ):
            return {'Contents': [
                {
                    'Key': (
                        'v0/xpcshell.dbg/A7D6F1BB18CD4CB48/xpcshell.sym'
                    ),
                    # based on `unzip -l tests/sample.zip` knowledge
                    'Size': 488,
                }
            ]}

        raise NotImplementedError((operation_name, api_params))

    with botomock(mock_api_call), open(ZIP_FILE, 'rb') as f:
        response = client.post(
            url,
            {'file.zip': f},
            HTTP_AUTH_TOKEN=token.key,
        )
        assert response.status_code == 201

        upload, = Upload.objects.all()
        assert upload.user == fakeuser
        # assert upload.inbox_key is None
        # assert expected_inbox_key_name_regex.findall(upload.inbox_filepath)
        assert upload.filename == 'file.zip'
        assert upload.completed_at
        # based on `ls -l tests/sample.zip` knowledge
        assert upload.size == 69812
        assert upload.bucket_name == 'private'
        assert upload.bucket_region is None
        assert upload.bucket_endpoint_url == 'https://s3.example.com'
        assert upload.skipped_keys == [
            'v0/south-africa-flag/deadbeef/south-africa-flag.jpeg',
            'v0/xpcshell.dbg/A7D6F1BB18CD4CB48/xpcshell.sym',
        ]
        assert upload.ignored_keys == ['build-symbols.txt']

    assert not FileUpload.objects.all().exists()


@pytest.mark.django_db
def test_upload_archive_by_url(
    client,
    botomock,
    fakeuser,
    metricsmock,
    settings,
    requestsmock
):

    requestsmock.head(
        'https://whitelisted.example.com/symbols.zip',
        text='Found',
        status_code=302,
        headers={
            'Location': 'https://download.example.com/symbols.zip',
        }
    )
    requestsmock.head(
        'https://whitelisted.example.com/bad.zip',
        text='Found',
        status_code=302,
        headers={
            'Location': 'https://bad.example.com/symbols.zip',
        }
    )

    settings.ALLOW_UPLOAD_BY_DOWNLOAD_DOMAINS = [
        'whitelisted.example.com',
        'download.example.com',
    ]
    token = Token.objects.create(user=fakeuser)
    permission, = Permission.objects.filter(codename='upload_symbols')
    token.permissions.add(permission)
    url = reverse('upload:upload_archive')

    def mock_api_call(self, operation_name, api_params):
        # This comes for the setting UPLOAD_DEFAULT_URL specifically
        # for tests.
        assert api_params['Bucket'] == 'private'
        if operation_name == 'HeadBucket':
            # yep, bucket exists
            return {}

        if (
            operation_name == 'ListObjectsV2' and
            api_params['Prefix'] == (
                'v0/south-africa-flag/deadbeef/south-africa-flag.jpeg'
            )
        ):
            # Pretend that we have this in S3 and its previous
            # size was 1000.
            return {'Contents': [
                {
                    'Key': (
                        'v0/south-africa-flag/deadbeef/south-africa-flag.jpeg'
                    ),
                    'Size': 1000,
                }
            ]}

        if (
            operation_name == 'ListObjectsV2' and
            api_params['Prefix'] == (
                'v0/xpcshell.dbg/A7D6F1BB18CD4CB48/xpcshell.sym'
            )
        ):
            # Pretend we don't have this in S3 at all
            return {}

        if (
            operation_name == 'PutObject' and
            api_params['Key'] == (
                'v0/south-africa-flag/deadbeef/south-africa-flag.jpeg'
            )
        ):
            assert 'ContentEncoding' not in api_params
            assert 'ContentType' not in api_params
            content = api_params['Body'].read()
            assert isinstance(content, bytes)
            # based on `unzip -l tests/sample.zip` knowledge
            assert len(content) == 69183

            # ...pretend to actually upload it.
            return {
                # Should there be anything here?
            }
        if (
            operation_name == 'PutObject' and
            api_params['Key'] == (
                'v0/xpcshell.dbg/A7D6F1BB18CD4CB48/xpcshell.sym'
            )
        ):
            # Because .sym is in settings.COMPRESS_EXTENSIONS
            assert api_params['ContentEncoding'] == 'gzip'
            # Because .sym is in settings.MIME_OVERRIDES
            assert api_params['ContentType'] == 'text/plain'
            body = api_params['Body'].read()
            assert isinstance(body, bytes)
            # If you look at the fixture 'sample.zip', which is used in
            # these tests you'll see that the file 'xpcshell.sym' is
            # 1156 originally. But we asser that it's now *less* because
            # it should have been gzipped.
            assert len(body) < 1156
            original_content = gzip.decompress(body)
            assert len(original_content) == 1156

            # ...pretend to actually upload it.
            return {}

        raise NotImplementedError((operation_name, api_params))

    with botomock(mock_api_call), open(ZIP_FILE, 'rb') as f:
        response = client.post(
            url,
            data={'url': 'http://example.com/symbols.zip'},
            HTTP_AUTH_TOKEN=token.key,
        )
        assert response.status_code == 400
        assert response.json()['error'] == 'Insecure URL'

        response = client.post(
            url,
            data={'url': 'https://notwhitelisted.example.com/symbols.zip'},
            HTTP_AUTH_TOKEN=token.key,
        )
        assert response.status_code == 400
        assert response.json()['error'] == (
            "Not an allowed domain ('notwhitelisted.example.com') to "
            "download from"
        )

        # More tricky, a URL that when redirecting, redirects
        # somewhere "bad".
        response = client.post(
            url,
            data={'url': 'https://whitelisted.example.com/bad.zip'},
            HTTP_AUTH_TOKEN=token.key,
        )
        assert response.status_code == 400
        assert response.json()['error'] == (
            "Not an allowed domain ('bad.example.com') to "
            "download from"
        )

        # Lastly, the happy path
        zip_file_content = f.read()
        requestsmock.head(
            'https://download.example.com/symbols.zip',
            content=b'',
            status_code=200,
            headers={
                'Content-Length': str(len(zip_file_content)),
            }
        )
        requestsmock.get(
            'https://download.example.com/symbols.zip',
            content=zip_file_content,
            status_code=200,
        )
        response = client.post(
            url,
            data={'url': 'https://whitelisted.example.com/symbols.zip'},
            HTTP_AUTH_TOKEN=token.key,
        )
        assert response.status_code == 201
        assert response.json()['upload']['download_url'] == (
            'https://download.example.com/symbols.zip'
        )

        upload, = Upload.objects.all()
        assert upload.download_url
        assert upload.user == fakeuser
        assert upload.filename == 'symbols.zip'
        assert upload.completed_at

    assert FileUpload.objects.filter(upload=upload).count() == 2

    # Check that markus caught timings of the individual file processing
    records = metricsmock.get_records()
    assert len(records) == 8
    assert records[0][0] == TIMING
    assert records[1][0] == TIMING
    assert records[2][0] == TIMING
    assert records[3][0] == TIMING
    assert records[4][0] == INCR
    assert records[5][0] == TIMING
    assert records[6][0] == INCR
    assert records[7][0] == TIMING


@pytest.mark.django_db
def test_upload_client_bad_request(fakeuser, client, settings):

    url = reverse('upload:upload_archive')
    response = client.get(url)
    assert response.status_code == 405
    error_msg = 'Method Not Allowed (GET): /upload/'
    assert response.json()['error'] == error_msg

    response = client.post(url)
    assert response.status_code == 403
    error_msg = 'This requires an Auth-Token to authenticate the request'
    assert response.json()['error'] == error_msg

    token = Token.objects.create(user=fakeuser)
    response = client.post(url, HTTP_AUTH_TOKEN=token.key)
    # will also fail because of lack of permission
    assert response.status_code == 403
    assert response.json()['error'] == 'Forbidden'

    # so let's fix that
    permission, = Permission.objects.filter(codename='upload_symbols')
    token.permissions.add(permission)

    response = client.post(url, HTTP_AUTH_TOKEN=token.key)
    assert response.status_code == 400
    error_msg = 'Must be multipart form data with at least one file'
    assert response.json()['error'] == error_msg

    # Upload an empty file
    empty_fileobject = BytesIO()
    response = client.post(
        url,
        {'myfile.zip': empty_fileobject},
        HTTP_AUTH_TOKEN=token.key,
    )
    assert response.status_code == 400
    assert response.json()['error'] == 'File size 0'

    # Unrecognized file extension
    with open(ZIP_FILE, 'rb') as f:
        response = client.post(
            url,
            {'myfile.rar': f},
            HTTP_AUTH_TOKEN=token.key,
        )
        assert response.status_code == 400
        assert response.json()['error'] == (
            'Unrecognized archive file extension ".rar"'
        )

    settings.DISALLOWED_SYMBOLS_SNIPPETS = ('xpcshell.sym',)

    with open(ZIP_FILE, 'rb') as f:
        response = client.post(
            url,
            {'file.zip': f},
            HTTP_AUTH_TOKEN=token.key,
        )
        assert response.status_code == 400
        error_msg = (
            "Content of archive file contains the snippet "
            "'xpcshell.sym' which is not allowed"
        )
        assert response.json()['error'] == error_msg

    # Undo that setting override
    settings.DISALLOWED_SYMBOLS_SNIPPETS = ('nothing',)

    # Now upload a file that doesn't have the right filename patterns
    with open(INVALID_ZIP_FILE, 'rb') as f:
        response = client.post(
            url,
            {'file.zip': f},
            HTTP_AUTH_TOKEN=token.key,
        )
        assert response.status_code == 400
        error_msg = (
            'Unrecognized file pattern. Should only be '
            '<module>/<hex>/<file> or <name>-symbols.txt and nothing else.'
        )
        assert response.json()['error'] == error_msg

    # Now upload a file that isn't a zip file
    with open(ACTUALLY_NOT_ZIP_FILE, 'rb') as f:
        response = client.post(
            url,
            {'file.zip': f},
            HTTP_AUTH_TOKEN=token.key,
        )
        assert response.status_code == 400
        error_msg = 'File is not a zip file'
        assert response.json()['error'] == error_msg


@pytest.mark.django_db
def test_upload_client_unrecognized_bucket(botomock, fakeuser, client):
    """The upload view raises an error if you try to upload into a bucket
    that doesn't exist."""
    token = Token.objects.create(user=fakeuser)
    permission, = Permission.objects.filter(codename='upload_symbols')
    token.permissions.add(permission)
    url = reverse('upload:upload_archive')

    def mock_api_call(self, operation_name, api_params):
        # This comes for the setting UPLOAD_DEFAULT_URL specifically
        # for tests.
        assert api_params['Bucket'] == 'private'
        if operation_name == 'HeadBucket':
            parsed_response = {
                'Error': {'Code': '404', 'Message': 'Not found'},
            }
            raise ClientError(parsed_response, operation_name)

        raise NotImplementedError((operation_name, api_params))

    with botomock(mock_api_call), open(ZIP_FILE, 'rb') as f:
        with pytest.raises(ImproperlyConfigured):
            client.post(
                url,
                {'file.zip': f},
                HTTP_AUTH_TOKEN=token.key,
            )


def test_get_bucket_info(settings):

    class FakeUser:
        def __init__(self, email):
            self.email = email

    user = FakeUser('peterbe@example.com')

    settings.UPLOAD_DEFAULT_URL = 'https://s3.amazonaws.com/some-bucket'
    bucket_info = get_bucket_info(user)
    assert bucket_info.name == 'some-bucket'
    assert bucket_info.endpoint_url is None
    assert bucket_info.region is None

    settings.UPLOAD_DEFAULT_URL = (
        'https://s3-eu-west-2.amazonaws.com/some-bucket'
    )
    bucket_info = get_bucket_info(user)
    assert bucket_info.name == 'some-bucket'
    assert bucket_info.endpoint_url is None
    assert bucket_info.region == 'eu-west-2'

    settings.UPLOAD_DEFAULT_URL = 'http://s3.example.com/buck/prefix'
    bucket_info = get_bucket_info(user)
    assert bucket_info.name == 'buck'
    assert bucket_info.endpoint_url == 'http://s3.example.com'
    assert bucket_info.region is None


def test_get_bucket_info_exceptions(settings):

    class FakeUser:
        def __init__(self, email):
            self.email = email

    settings.UPLOAD_DEFAULT_URL = 'https://s3.amazonaws.com/buck'
    settings.UPLOAD_URL_EXCEPTIONS = {
        'peterbe@example.com': 'https://s3.amazonaws.com/differenting',
        't*@example.com': 'https://s3.amazonaws.com/excepty',
    }

    user = FakeUser('Peterbe@example.com')
    bucket_info = get_bucket_info(user)
    assert bucket_info.name == 'differenting'

    user = FakeUser('Tucker@example.com')
    bucket_info = get_bucket_info(user)
    assert bucket_info.name == 'excepty'


def test_UploadByDownloadForm_happy_path(requestsmock, settings):
    settings.ALLOW_UPLOAD_BY_DOWNLOAD_DOMAINS = ['whitelisted.example.com']

    requestsmock.head(
        'https://whitelisted.example.com/symbols.zip',
        content=b'content',
        status_code=200,
        headers={
            'Content-Length': '1234',
        }
    )

    form = UploadByDownloadForm({
        'url': 'https://whitelisted.example.com/symbols.zip',
    })
    assert form.is_valid()
    assert form.cleaned_data['url'] == (
        'https://whitelisted.example.com/symbols.zip'
    )
    assert form.cleaned_data['upload']['name'] == 'symbols.zip'
    assert form.cleaned_data['upload']['size'] == 1234


def test_UploadByDownloadForm_connectionerrors(requestsmock, settings):
    settings.ALLOW_UPLOAD_BY_DOWNLOAD_DOMAINS = [
        'whitelisted.example.com',
        'download.example.com',
    ]

    requestsmock.head(
        'https://whitelisted.example.com/symbols.zip',
        exc=ConnectionError,
    )

    form = UploadByDownloadForm({
        'url': 'https://whitelisted.example.com/symbols.zip',
    })
    assert not form.is_valid()
    validation_errors, = form.errors.as_data().values()
    assert validation_errors[0].message == (
        'ConnectionError trying to open '
        'https://whitelisted.example.com/symbols.zip'
    )

    # Suppose the HEAD request goes to another URL which eventually
    # raises a ConnectionError.

    requestsmock.head(
        'https://whitelisted.example.com/redirect.zip',
        text='Found',
        status_code=302,
        headers={
            'Location': 'https://download.example.com/busted.zip'
        }
    )
    requestsmock.head(
        'https://download.example.com/busted.zip',
        exc=ConnectionError,
    )
    form = UploadByDownloadForm({
        'url': 'https://whitelisted.example.com/redirect.zip',
    })
    assert not form.is_valid()
    validation_errors, = form.errors.as_data().values()
    assert validation_errors[0].message == (
        'ConnectionError trying to open '
        'https://download.example.com/busted.zip'
    )

    # Suppose the URL simply is not found.
    requestsmock.head(
        'https://whitelisted.example.com/404.zip',
        text='Not Found',
        status_code=404,
    )
    form = UploadByDownloadForm({
        'url': 'https://whitelisted.example.com/404.zip',
    })
    assert not form.is_valid()
    validation_errors, = form.errors.as_data().values()
    assert validation_errors[0].message == (
        "https://whitelisted.example.com/404.zip can't be found (404)"
    )
