#!/usr/bin/python
# Copyright 2015 Google Inc. All Rights Reserved.
"""A python wrapper to start devappserver and a managed vm application.

Both the devappserver and the application will run in their respective
containers.
"""
# This file conforms to the external style guide
# pylint: disable=bad-indentation

import argparse
import logging
import sys
import time

from appstart import container_sandbox
from appstart import devappserver_init


def main():
    """Run devappserver and the user's application in separate containers.

    The application must be started with the proper environment variables,
    port bindings, and volume bindings. The devappserver image runs a
    standalone api server.
    """
    logging.getLogger('appstart').setLevel(logging.INFO)
    if 'init' in sys.argv:
        args = make_init_parser().parse_args(sys.argv[2:])
        if args.source == 'local':
            devappserver_init.base_image_from_root()
        else:
            # It might be preferable to add an init version
            # where the devappserver Dockerfile pulls the whole
            # gcloud sdk in.
            print ('NOT SUPPORTED: Building the dev_appserver'
                   'from an external source is currently not supported.')
    else:
        args = make_appstart_parser().parse_args()
        try:
            with container_sandbox.ContainerSandbox(**vars(args)):
                while True:
                    time.sleep(10000)
        except KeyboardInterrupt:
            logging.info('Appstart terminated by user.')


def make_init_parser():
    """Make an argument parser for the init subcommand.

    Returns:
        (argparse.ArgumentParser) a parser for the init subcommand.
    """
    parser = argparse.ArgumentParser(
        description='Build the base devappserver image.')
    parser.add_argument('--source',
                        default='local',
                        choices=['local', 'remote'],
                        help='Specify the location of the gcloud '
                        'sdk source.')
    return parser


def make_appstart_parser():
    """Make an argument parser to take in command line arguments.

    Returns:
        (argparse.ArgumentParser) the parser.
    """
    parser = argparse.ArgumentParser(
        description='Wrapper to run a managed vm container. If '
        'using for the first time, run \'appstart init\' '
        'to generate a devappserver base image.')
    parser.add_argument('--image_name',
                        default=None,
                        help='The name of the docker image to run. '
                        'If the docker image is specified, no '
                        'new docker image will be built from the '
                        'application\'s Dockerfile.')
    parser.add_argument('--run_api_server',
                        choices=['True', 'true', 'False', 'false'],
                        nargs=1,
                        default='True',
                        action=BoolAction,
                        help='If false, appstart will not start the '
                        'api server.')
    parser.add_argument('--app_port',
                        default=8080,
                        type=int,
                        help='The port where the application should be '
                        'reached externally.')
    parser.add_argument('--admin_port',
                        default='8000',
                        type=int,
                        help='The port where the admin panel should be '
                        'reached externally. ')
    parser.add_argument('--application_id',
                        default='temp',
                        help='The id of the application.'
                        'This id will determine what datastore '
                        'the application will have access to '
                        'and should be the same as the Google '
                        'App Engine id, which can be found on the '
                        'developers\' console.')
    parser.add_argument('--storage_path',
                        default='/tmp/appengine/storage',
                        help='The directory where the application '
                        'files should get stored. This includes '
                        'the Datastore, logservice files, Google Cloud '
                        'Storage files, etc. ')

    # The port that the admin panel should bind to inside the container.
    parser.add_argument('--internal_admin_port',
                        type=int,
                        default=32768,
                        help=argparse.SUPPRESS)

    # The port that the api server should bind to inside the container.
    parser.add_argument('--internal_api_port',
                        type=int,
                        default=32769,
                        help=argparse.SUPPRESS)

    # The port that the proxy should bind to inside the container.
    parser.add_argument('--internal_proxy_port',
                        type=int,
                        default=32770,
                        help=argparse.SUPPRESS)

    parser.add_argument('--log_path',
                        default='/tmp/log/appengine',
                        help='The location where this container will '
                        'output logs.')
    parser.add_argument('--use-cache',
                        choices=['True', 'true', 'False', 'false'],
                        nargs=1,
                        default='True',
                        action=BoolAction,
                        help='If false, docker will not use '
                        'the cache during image builds.')
    parser.add_argument('config_file',
                        help='The relative or absolute path to the '
                        'application\'s .yaml or .xml file.')
    return parser


# pylint: disable=too-few-public-methods
class BoolAction(argparse.Action):
    """Action to parse boolean values."""

    def __init__(self, option_strings, dest, nargs=None, **kwargs):
        """Call constructor of arpargse.Action."""
        super(BoolAction, self).__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        """Parse boolean arguments and populate namespace."""
        if values in ['True', 'true']:
            setattr(namespace, self.dest, True)
        elif values in ['False', 'false']:
            setattr(namespace, self.dest, False)

if __name__ == '__main__':
    main()
