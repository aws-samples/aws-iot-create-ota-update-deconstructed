# Introduction

The [CreateOTAUpdate](https://docs.aws.amazon.com/iot/latest/apireference/API_CreateOTAUpdate.html) operation is used by the AWS IoT console, the [AWS CLI](https://docs.aws.amazon.com/cli/latest/reference/iot/create-ota-update.html) and the [AWS SDKs](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/iot/client/create_ota_update.html) to create [AWS IoT Over the Air (OTA)](https://www.freertos.org/ota/index.html) updates.

At the time of writing, the [CreateOTAUpdate](https://docs.aws.amazon.com/iot/latest/apireference/API_CreateOTAUpdate.html) operation does not support the following advanced job configuration features:

- [automated retries](https://aws.amazon.com/about-aws/whats-new/2022/01/aws-iot-device-management-automated-retry-capability-jobs-improve-success-rates-large-scale-deployments/)
- [job scheduling](https://aws.amazon.com/about-aws/whats-new/2022/11/aws-iot-device-management-jobs-now-supports-scheduling-configuration/)
- [recurring maintenance windows](https://aws.amazon.com/about-aws/whats-new/2023/03/aws-iot-device-management-jobs-maintenance-window-feature/)
- [Software Package Catalog](https://aws.amazon.com/about-aws/whats-new/2023/06/aws-iot-device-management-software-package-catalog/)

The purpose of this repository is to demonstrate an alternate method of creating an OTA update that can utilize these advanced job configuration features.

# CreateOTAUpdate Deconstructed

AWS IoT OTA update is a feature built on top of [AWS IoT Jobs](https://docs.aws.amazon.com/iot/latest/developerguide/iot-jobs.html). An OTA update is a job, but with a pre-defined job document. The [CreateOTAUpdate](https://docs.aws.amazon.com/iot/latest/apireference/API_CreateOTAUpdate.html) API operation is an integrated operation that bundles several API operations into a single call. Depending on the parameters you pass, **CreateOTAUpdate** performs the following steps:

1. If the user does NOT use custom signing, **CreateOTAUpdate** calls [StartingSigningJob](https://docs.aws.amazon.com/signer/latest/api/API_StartSigningJob.html) and [DescribeSigningJob](https://docs.aws.amazon.com/signer/latest/api/API_DescribeSigningJob.html) to sign a firmware binary file (or files) and places the signed result into an S3 bucket.
2. If the user has NOT already created a stream, **CreateOTAUpdate** calls [CreateStream](https://docs.aws.amazon.com/iot/latest/apireference/API_CreateStream.html) to create an [MQTT file stream](https://docs.aws.amazon.com/iot/latest/developerguide/mqtt-based-file-delivery.html) for the firmware binary file in S3.
3. Assembles an appropriate job document based on earlier steps.
4. Uses [CreateJob](https://docs.aws.amazon.com/iot/latest/apireference/API_CreateJob.html) to create an AWS IoT Job.

You can use the knowledge of how **CreateOTAUpdate** is composed, to create your own jobs that are equivalent to an OTA update. This gives you access to all of the advanced job configurations that are exposed by the **CreateJob** operation. This repository provides an example of precisely that.

# Requirements

The example in this repository is a Python script. Package dependencies can be resolved as follows:

```bash
pip3 install -r requirements.txt
```

Please consider to use a [virtual environment](https://docs.python.org/3/library/venv.html).

# Required IAM Permissions

Two distinct principals are involved, and each needs its own permissions:

1. **The operator**: the IAM user or role whose credentials run `create_ota_update.py`.
2. **The OTA service role**: the role named by the `otaRole` argument, which AWS IoT assumes to read the firmware object from S3 when it creates the stream.

Both are described below. Replace `MyBucketName`, `MyIoTOTARoleName`, the account ID `012345678901`, and the region `us-east-1` with your own values.

## Operator policy

The operator runs each API call in the script directly. The script calls `sts:GetCallerIdentity` (to resolve the account ID), reads and copies the S3 object, lists its versions, signs it, and creates the stream and job. Because the script passes the OTA service role to `iot:CreateStream`, the operator also needs `iam:PassRole` scoped to that role.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Identity",
      "Effect": "Allow",
      "Action": "sts:GetCallerIdentity",
      "Resource": "*"
    },
    {
      "Sid": "S3ObjectAccess",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:GetObjectVersion",
        "s3:PutObject"
      ],
      "Resource": "arn:aws:s3:::MyBucketName/*"
    },
    {
      "Sid": "S3ListVersions",
      "Effect": "Allow",
      "Action": "s3:ListBucketVersions",
      "Resource": "arn:aws:s3:::MyBucketName"
    },
    {
      "Sid": "CodeSigning",
      "Effect": "Allow",
      "Action": [
        "signer:StartSigningJob",
        "signer:DescribeSigningJob"
      ],
      "Resource": "*"
    },
    {
      "Sid": "IoTJobsAndStreams",
      "Effect": "Allow",
      "Action": [
        "iot:CreateStream",
        "iot:CreateJob"
      ],
      "Resource": "*"
    },
    {
      "Sid": "PassOtaRole",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::012345678901:role/MyIoTOTARoleName"
    }
  ]
}
```

Notes:
- A copy (`s3.copy_object`) is a read of the source plus a write of the destination, so it needs `s3:GetObject` and `s3:PutObject` rather than a single `s3:CopyObject` action (there is no such action).
- The object-level actions are scoped to the object ARN (`arn:aws:s3:::MyBucketName/*`), while `s3:ListBucketVersions` is scoped to the bucket ARN (`arn:aws:s3:::MyBucketName`).
- `signer:*` and `iot:CreateStream`/`iot:CreateJob` do not support useful resource-level scoping for these calls, so `Resource` is `*`.

## OTA service role

The `otaRole` is assumed by AWS IoT to read the (signed) firmware object from S3 when creating the stream. Attach a permissions policy granting read access to the bucket:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:GetObjectVersion"
      ],
      "Resource": "arn:aws:s3:::MyBucketName/*"
    }
  ]
}
```

The role also needs a trust policy that allows AWS IoT to assume it:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "iot.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

# Usage

The [create_ota_update.py](create_ota_update.py) script creates an IoT job that is the equivalent of an OTA update. The usage is shown below.

```text
@ubuntu:~/git/create-ota-update-deconstructed$ python create_ota_update.py -h
usage: create_ota_update.py [-h] binary bucket signingProfile otaRole thingGroup jobId

Create and execute AWS IoT OTA update directly using jobs

positional arguments:
  binary          Name of the binary file (object key) in the S3 bucket
  bucket          Name of the bucket
  signingProfile  Name of the signing profile
  otaRole         Name of the IAM role that grants IoT access to S3, IoT jobs and code signing
  thingGroup      Name of the target Thing group
  jobId           ID of the job to be created ('AFR_OTA-' will be prepended)

optional arguments:
  -h, --help      show this help message and exit
```

# Equivalent CreateOTAUpdate

The steps implemented in [create_ota_update.py](create_ota_update.py) are the equivalent of a CreateOTAUpdate call constructed as follows (but with retries and scheduling added):

```python
    response = iot.create_ota_update(
        otaUpdateId = 'MyJobId',
        targets = ['arn:aws:iot:us-east-1:012345678901:thinggroup/MyThingGroupName'],
        files = [
            {
                'fileLocation': {
                    's3Location': {
                        'bucket': 'MyBucketName',
                        'key': 'MyBinaryFileName',
                        'version': '<version>'
                    }
                },
                'codeSigning': {
                    'startSigningJobParameter': {
                        'signingProfileName': 'MySigningProfileName'
                    }
                }
            }
        ],
        awsJobTimeoutConfig = {
            'inProgressTimeoutInMinutes': 10
        },
        protocols = ['MQTT'],
        roleArn = 'arn:aws:iam::012345678901:role/MyIoTOTARoleName'
    )
```

The following scenario is demonstrated:

1. MQTT as the file transfer protocol.
2. An unsigned binary file in S3 (rather than a pre-existing stream or a previously signed file).
3. Signing performed using a signing profile (rather than a certificate or custom signing).
4. Each device having 10 minutes to complete a job execution before it times out.
5. Number of [retries](https://docs.aws.amazon.com/iot/latest/apireference/API_CreateJob.html#iot-CreateJob-request-jobExecutionsRetryConfig) set to 3.
6. A [scheduled job](https://docs.aws.amazon.com/iot/latest/apireference/API_CreateJob.html#iot-CreateJob-request-schedulingConfig) that starts one hour in the future and ends an hour later.

The example can of course be adjusted to cater for different options and to use additional advanced job configuration such as [recurring maintenance windows](https://docs.aws.amazon.com/iot/latest/apireference/API_SchedulingConfig.html#iot-Type-SchedulingConfig-maintenanceWindows) and [software catalog package versions](https://docs.aws.amazon.com/iot/latest/apireference/API_CreateJob.html#iot-CreateJob-request-destinationPackageVersions).

# Deviations

The resultant jobs and artifacts deviate from **CreateOTAUpdate** only in the fact that no OTAUpdate resource is created. Thus the [ListOTAUpdates](https://docs.aws.amazon.com/iot/latest/apireference/API_ListOTAUpdates.html), [GetOTAUpdate](https://docs.aws.amazon.com/iot/latest/apireference/API_GetOTAUpdate.html) and [DeleteOTAUpdate](https://docs.aws.amazon.com/iot/latest/apireference/API_DeleteOTAUpdate.html) operations cannot be used to manage the resultant job.

# Signed Object

The signing job creates a signed object in S3 which is just a JSON document of the following structure:

```json
{
    "rawPayloadSize":366816,
    "signature":"MEUCIQCuwPQBzaKu9Rp4v4BwGa7T4h3JK71KwDJ3LZoSvYdCsQIgclDeaFE+5NeJL3cydMiuKM909bprNXfCZcneIuiSZrk=",
    "signatureAlgorithm":"SHA256withECDSA",
    "payload":"U0ZVTQEAAQDgkAUAAAAAAOCQBQA+0Ac4CWNDyJ0zb+koNKotI2yKdpcEQX6MT3ozwlfdAD7QBzgJY0PInTNv6Sg0qi0jbIp2lwRBfoxPejPCV90AAAAAAAAAAAAAAAAAAAAAAAAAAAAA...."
}
```

All fields of course depend on the file being signed and the signing algorithm used.

Note that **CreateOTAUpdate** overwrites the signed object with a copy of the raw binary file object. Thus the object has two versions; the newer version is the raw binary, the older version is the above JSON file (with **base64** encoded firmware). The stream is created from the raw binary (the newer version of the object).

# Job Document

The resulting job document has a structure similar to:

```json
{
  "afr_ota": {
    "protocols": [
      "MQTT"
    ],
    "streamname": "AFR_OTA-6e01bdb2-5057-4606-8966-b1bf8028416f",
    "files": [
      {
        "filepath": null,
        "filesize": 366816,
        "fileid": 0,
        "certfile": "foobar",
        "update_data_url": null,
        "auth_scheme": null,
        "sig-sha256-ecdsa": "MEUCIQCuwPQBzaKu9Rp4v4BwGa7T4h3JK71KwDJ3LZoSvYdCsQIgclDeaFE+5NeJL3cydMiuKM909bprNXfCZcneIuiSZrk="
      }
    ]
  }
}
```

The signature (**sig-sha256-ecdsa** in this case) and **filesize** values are taken from the signed object JSON. Note that the key of the signature depends on the signing algorithms used.

The **auth_scheme** and **update_data_url** are null when the protocol is MQTT.

The **filepath** value is device specific and not used on most devices.

The **certfile** value should match the **certname** value that was used when [creating the signing profile](https://docs.aws.amazon.com/signer/latest/api/API_PutSigningProfile.html).

# Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

# License

This library is licensed under the MIT-0 License. See the LICENSE file.
