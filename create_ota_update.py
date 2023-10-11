# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""
This script demonstrates how you can create an OTA Update without calling the
CreateOTAUpdate API operation. CreateOTAUpdate is an integrated operation that
makes numerous other API calls, finishing with CreateJob. This example makes
those APIs calls to construct a job and job document that is equivalent to
an OTA Update. This approach allows you to perform an OTA Update that uses
advanced job configuration options such as retries, scheduling and Software
Package Catalog. These options are not supported by CreateOTAUpdate, but are
supported by CreateJob.

This example creates the OTA Update job with the following paraemters and scenario:
    - MQTT as the protocol
    - an unsigned binary in S3 (which it will sign and create a stream for)
    - signing performed using a signing profile (not a certificate or custom signing)
    - 10 minute timeout on the job executions
    - 3 retries
    - scheduled job that starts one hour in the future and ends an hour later

Example execution:
python3 create_ota_update.py MyBinaryFileName MyBucketName MySigningProfileName
                                MyIoTOTARoleName MyThingGroupName MyJobId
"""

import time
import argparse
import uuid
import json
from datetime import datetime, timedelta, timezone
import boto3

ACCOUNT = boto3.client('sts').get_caller_identity().get('Account')
REGION = boto3.session.Session().region_name

def get_s3_object_version(key):
    """ Gets the (latest) version ID of an S3 object """
    print("Finding object " + key + " in S3 bucket " + args.bucket)
    response = s3.get_object(Bucket = args.bucket, Key = key)
    return response['VersionId']


def create_signing_job():
    """ Creates a signing job to sign the firmware binary """
    print('Start signing job')
    response = signer.start_signing_job(
        source = {
            's3': {
                'bucketName': args.bucket,
                'key': args.binary,
                'version': get_s3_object_version(args.binary)
            }
        },
        destination = {
            's3': {
                'bucketName': args.bucket,
                'prefix': 'SignedImage/'
            }
        },
        profileName = args.signingProfile
    )

    job_id = response['jobId']
    done = False

    while not done:
        response = signer.describe_signing_job(jobId = job_id)
        done = response['status'] != 'InProgress'
        time.sleep(0.1)

    print(f'Signing job {response["status"]} ({response["statusReason"]})')
    print(f'Created signed object {response["signedObject"]["s3"]["bucketName"]}'\
            ' in {response["signedObject"]["s3"]["key"]}')
    return response


def create_stream():
    """ Creates a stream for the signed firmware binary """
    # CreateOTAUpdate copies the raw binary file over the top of the signed
    # object (creating two versions). The stream is streaming the latest version
    # which is the raw binary file. So we perform this copy before creating the stream.
    s3.copy_object(Bucket = signing_job['signedObject']['s3']['bucketName'],
                    Key = signing_job['signedObject']['s3']['key'],
                    CopySource = {'Bucket': args.bucket, 'Key': args.binary})

    print('Creating stream')
    response = iot.create_stream(
        streamId = f'AFR_OTA-{str(uuid.uuid4())}',
        description = f'Stream for deconstructed OTAUpdate {args.jobId}',
        files = [
            {
                'fileId': 0,
                's3Location': {
                    'bucket': signing_job['signedObject']['s3']['bucketName'],
                    'key': signing_job['signedObject']['s3']['key'],
                    'version': get_s3_object_version(signing_job['signedObject']['s3']['key'])
                }
            },
        ],
        roleArn = f'arn:aws:iam::{ACCOUNT}:role/{args.otaRole}'
    )

    print(f'Created stream {response["streamId"]}')
    return response


JOB_DOCUMENT = \
'''
{
  "afr_ota": {
    "protocols": [
      "MQTT"
    ],
    "streamname": "placeholder",
    "files": [
      {
        "filepath": null,
        "filesize": 0,
        "fileid": 0,
        "certfile": "placeholder",
        "update_data_url": null,
        "auth_scheme": null
      }
    ]
  }
}
'''

def create_job():
    """ Creates an AWS IoT job from the signed object and the stream """
    # As we copied the raw binary over the signed object, there's now two versions of the
    # signed object in S3. The newer is the raw binary that will be streamed. The older
    # is the signed object. This is a JSON file with the binary file encoded as base64.
    # Other JSON elements are the signature (base64) and the raw payload size.
    print('Extracting signature from signed S3 object')
    signed_object_versions = s3.list_object_versions(
                                Bucket = signing_job['signedObject']['s3']['bucketName'],
                                Prefix = signing_job['signedObject']['s3']['key']
                                )['Versions']
    response = s3.get_object(
                    Bucket = signing_job['signedObject']['s3']['bucketName'],
                    Key = signing_job['signedObject']['s3']['key'],
                    VersionId = signed_object_versions[-1]['VersionId']
                    )
    signed_object_dict = json.loads(response['Body'].read().decode('utf-8'))
    print(f'Signature = {signed_object_dict["signature"]}')

    # Fill in the job document. For simplicity, we assume only one file in the job, hence index 0.
    # We add a key for the signature. The key name depends on the signing algorithn used by the
    # signing job. For example, if the signing algortihm is "SHA256withESDSA" then the
    # signature key is 'sig-sha256-ecdsa'.
    job_document_dict = json.loads(JOB_DOCUMENT)
    job_document_dict['afr_ota']['streamname'] = stream['streamId']
    job_document_dict['afr_ota']['files'][0]['filesize'] = signed_object_dict['rawPayloadSize']
    job_document_dict['afr_ota']['files'][0]['certfile'] = \
        signing_job['signingParameters']['certname']
    sig_key = f'sig-{signed_object_dict["signatureAlgorithm"].replace("with", "-").lower()}'
    job_document_dict['afr_ota']['files'][0][sig_key] = signed_object_dict['signature']
    print('Job Document:')
    print(job_document_dict)

    now = datetime.now(timezone.utc)

    print('Creating job')
    response = iot.create_job(
        jobId = f'AFR_OTA-{args.jobId}',
        targets = [f'arn:aws:iot:{REGION}:{ACCOUNT}:thinggroup/{args.thingGroup}'],
        document = json.dumps(job_document_dict),
        description = f'AWS job for deconstructed OTAUpdateJobId = {args.jobId}',
        targetSelection = 'SNAPSHOT',
        timeoutConfig = {
            'inProgressTimeoutInMinutes': 10
        },
        jobExecutionsRetryConfig={
            'criteriaList': [
                {
                    'failureType': 'TIMED_OUT',
                    'numberOfRetries': 3
                },
            ]
        },
        schedulingConfig={
            'endBehavior': 'CANCEL',
            'endTime': (now + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M'),
# If this were a CONTINUOUS job, you could optionally define recurring maintenance windows here
#            'maintenanceWindows': [
#                {
#                    'startTime': 'string',
#                    'durationInMinutes': 123
#                },
#            ],
            'startTime': (now + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
        }
# You may optionally define Software Package Catalog package version ARNs here
#        destinationPackageVersions=[
#            'string',
#        ]
    )
    print(f'Created job {response["jobId"]}')


parser = argparse.ArgumentParser(description='Create and execute AWS IoT OTA update using jobs')
parser.add_argument('binary',           help='Name of the binary file (object key)\
                                                in the S3 bucket')
parser.add_argument('bucket',           help='Name of the bucket')
parser.add_argument('signingProfile',   help='Name of the signing profile')
parser.add_argument('otaRole',          help='Name of the IAM role that grants IoT access\
                                                to S3, IoT jobs and code signing')
parser.add_argument('thingGroup',       help='Name of the target Thing group')
parser.add_argument('jobId',            help='ID of the job to be created (\'AFR_OTA-\' will\
                                                be prepended)')
args = parser.parse_args()

iot = boto3.client('iot')
s3 = boto3.client('s3')
signer = boto3.client('signer')

signing_job = create_signing_job()
stream = create_stream()
create_job()
