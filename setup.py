import aircon
import setuptools
from os import path

this_directory = path.abspath(path.dirname(__file__))

with open(path.join(this_directory, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setuptools.setup(
    name='aircon',
    version=aircon.__version__,
    description='Interface for controlling Air Conditioners, e.g. with HiSense modules.',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/deiger/AirCon',
    author='Dror Eiger',
    author_email='droreiger@gmail.com',
    license='GPL 3.0',
    packages=setuptools.find_packages(),
    install_requires=[
          'aiohttp>=3.6.1, <=3.6.2',
          'dataclasses_json',
          'pycryptodome',
          'paho-mqtt==1.5.0',
          'tenacity'
      ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        "Topic :: Home Automation",
    ],
)

