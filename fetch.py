import pandas as pd
import os
import shutil
import requests
import subprocess
import hashlib
import muda
import jams
import sys

# Prerequisites
# - mp3splt is installed
# - gstreamer is installed with following plugins:
#   - gst-plugins-base
#   - gst-plugins-good
#   - gst-plugins-bad
#   - gst-plugins-ugly (seems to be in use)

SECONDS_IN_MINUTE = 60

GROUP_ATTR = 'Group'
LANGUAGE_ATTR = 'Language'
SEX_ATTR = 'Sex'
URL_ATTR = 'Url'

MP3_EXTENSION = '.mp3'

TEMP_DIR = '.temp'

INPUT_FILE = 'input' + MP3_EXTENSION
TRIMMED_PATTERN = 'trimmed{extension}'

INPUT_OFFSET = 30  # seconds

TRAIN_INPUT_DURATION_MIN = 5  # minutes
TEST_INPUT_DURATION_MIN = TRAIN_INPUT_DURATION_MIN * 3  # minutes

FRAG_DURATION_SEC = 10  # seconds
FRAG_PATTERN = "{lang}_{sex}_{url_hash}_{index}{extension}"

# `-n`: remove metadata
TRIM_CMD = "mp3splt {input} 0.{offset}.0 {duration}.{offset}.0 -n -o {output}"
SPLIT_CMD = "mp3splt {input} -t 0.{duration}.0 -o {output}"

JAMS_FILE = "{0}/test.jams".format(TEMP_DIR)
NOISES_DIR = "noises"
NOISE_FILES = [
    "{0}/car.flac".format(NOISES_DIR),
    "{0}/birds.flac".format(NOISES_DIR),
    "{0}/coffee_shop.flac".format(NOISES_DIR),
    "{0}/street_traffic.flac".format(NOISES_DIR)
]


pitch_deformer = muda.deformers.PitchShift(n_semitones=[-2, -1, 1, 2])

speed_deformer = muda.deformers.TimeStretch(rate=[0.81, 0.93, 1.07, 1.23])

# BackgroundNoise loads noise files and split them into chunks.
# It takes significant amount of time.
# Cache deformer to speed up this process.
noise_deformer = muda.deformers.BackgroundNoise(n_samples=3, files=NOISE_FILES, weight_min=0.1, weight_max=0.2)


def run_in_foreground(cmd):
    process = subprocess.Popen(cmd, shell=True)

    process.communicate()

    if process.wait() != 0:
        print("An error occurs whilst executing command:\n{0}".format(cmd))
        raise SystemExit


def fetch_resource(url, path):
    response = requests.get(url, stream=True)
    with open(path, 'wb') as file:
        shutil.copyfileobj(response.raw, file)
    del response


def deform(input):
    jam = jams.JAMS()

    audio = muda.load_jam_audio(jam, input, res_type='kaiser_fast')

    input_without_ext = os.path.splitext(input)[0]

    # can't force muda to use mp3 format. use ogg instead.

    # index + 1: start from 1 to be compatible with mp3splt file names

    # negative: deep voice
    print("Creating pitch variations...")
    for index, output in enumerate(pitch_deformer.transform(audio)):
        muda.save('{0}_pitch_{1}.ogg'.format(input_without_ext, index + 1), JAMS_FILE, output)

    # less 1.0: longer
    print("Creating speed variations...")
    for index, output in enumerate(speed_deformer.transform(audio)):
        muda.save('{0}_speed_{1}.ogg'.format(input_without_ext, index + 1), JAMS_FILE, output)

    print("Creating noise variations...")
    for index, output in enumerate(noise_deformer.transform(audio)):
        muda.save('{0}_noise_{1}.ogg'.format(input_without_ext, index + 1), JAMS_FILE, output)


# https://stackoverflow.com/questions/16694907/how-to-download-large-file-in-python-with-requests-py
def download_file(url):
    local_filename = url.split('/')[-1]
    # NOTE the stream=True parameter
    r = requests.get(url, stream=True)
    with open(local_filename, 'wb') as f:
        for chunk in r.iter_content(chunk_size=1024):
            if chunk:  # filter out keep-alive new chunks
                f.write(chunk)
                # f.flush() commented by recommendation from J.F.Sebastian
    return local_filename


data = pd.read_csv('data.csv')

groups = data[GROUP_ATTR].unique()
for group in groups:
    if os.path.isdir(group):
        shutil.rmtree(group)
    os.mkdir(group)

for sample_index, row in data.iterrows():
    sample_index += 1

    group = row[GROUP_ATTR]
    language = row[LANGUAGE_ATTR]
    sex = row[SEX_ATTR][0]  # first letter, i.e. `f` or `m`
    url = row[URL_ATTR]

    is_test = group == 'test'
    if is_test:
        input_duration_min = TEST_INPUT_DURATION_MIN
    else:
        input_duration_min = TRAIN_INPUT_DURATION_MIN

    frag_count = (input_duration_min * SECONDS_IN_MINUTE) // FRAG_DURATION_SEC
    url_hash = hashlib.md5(url.encode()).hexdigest()
    input_path = os.path.join(TEMP_DIR, INPUT_FILE)
    trimmed_path = os.path.join(TEMP_DIR, TRIMMED_PATTERN.format(extension=MP3_EXTENSION))
    trimmed_pattern = TRIMMED_PATTERN.format(extension='')
    frag_pattern = FRAG_PATTERN.format(lang=language, sex=sex, url_hash=url_hash, index='@n', extension='')

    print("===> Sample #{0}: {1}".format(sample_index, frag_pattern))

    if os.path.isdir(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)
    os.mkdir(TEMP_DIR)

    try:
        print("Downloading a sample...")
        fetch_resource(url, input_path)

        print("Trimming a sample...")
        run_in_foreground(TRIM_CMD.format(input=input_path, output=trimmed_pattern,
                                          offset=INPUT_OFFSET, duration=input_duration_min))

        print("Splitting a sample into smaller chunks")
        run_in_foreground(SPLIT_CMD.format(input=trimmed_path, output=frag_pattern,
                                           duration=FRAG_DURATION_SEC))

        for frag_index in range(0, frag_count):
            frag_index += 1

            print('======> Fragment #{0}'.format(frag_index))

            # mp3splt creates index starting from 1
            frag_index_with_padding = str(frag_index).zfill(2)
            filename = FRAG_PATTERN.format(lang=language, sex=sex, url_hash=url_hash, index=frag_index_with_padding,
                                           extension=MP3_EXTENSION)
            source_path = os.path.join(TEMP_DIR, filename)
            target_path = os.path.join(group, filename)

            # copy frag file to test/valid/test dir
            shutil.copy2(source_path, target_path)

            if not is_test:
                deform(target_path)
    except:
        print("Unexpected error:", sys.exc_info()[0])


# remove temporary files
shutil.rmtree(TEMP_DIR)