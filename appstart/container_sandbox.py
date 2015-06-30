# Copyright 2015 Google Inc. All Rights Reserved.
"""A ContainerSandbox manages the application and devappserver containers.

This includes their creation, termination, and destruction.
ContainerSandbox is intended to be used inside a "with" statement. Inside
the interior of the "with" statement, the user interact with the containers
via the docker api. It may also be beneficial for the user to perform
system tests in this manner.
"""
# This file conforms to the external style guide
# pylint: disable=bad-indentation, g-bad-import-order

import io
import os
import requests
import sys
import time
import urlparse

import docker

import utils
from utils import get_logger

# Devappserver base image
DEVAPPSERVER_IMAGE = 'appstart_devappserver_base'

# Maximum attempts to health check application container.
MAX_ATTEMPTS = 30

# Yaml file error message
YAML_MSG = 'The yaml file must be in the application\'s root directory.'

# XML file error message
XML_MSG = 'The xml file must be in the WEB-INF directory.'

# Default port that the application is expected to listen on inside
# the application container.
DEFAULT_APPLICATION_PORT = 8080

# Time format for naming images/containers
TIME_FMT = '%Y.%m.%d_%H.%M.%S'

# Java offset for the xml file's location, relative to the root
# diretory of the WAR archive
JAVA_OFFSET = 'WEB-INF/'


class ContainerSandbox(object):
    """Sandbox to manage the user application & devappserver containers.

    This sandbox aims to leave the docker container space untouched.
    Proper usage ensures that application & devappserver containers will
    be created, started, stopped, and destroyed. For proper usage, the
    ContainerSandbox should be used as a context manager (inside a "with"
    statement), or the start and stop functions should be invoked from
    within a try-finally context.
    """
    # pylint: disable=too-many-instance-attributes
    # pylint: disable=too-many-arguments

    def __init__(self,
                 config_file,
                 admin_port=8000,
                 application_id='temp',
                 app_port=8080,
                 image_name=None,
                 internal_admin_port=32768,
                 internal_api_port=32769,
                 internal_proxy_port=32770,
                 log_path='/tmp/log/appengine',
                 run_api_server=True,
                 storage_path='/tmp/appengine/storage',
                 use_cache=True):
        """Get the sandbox ready to construct and run the containers.

        Args:
            config_file: (basestring) The relative or full path
                to the config_file of the application. If image_name is
                not specified, this path will be used to help find the
                Dockerfile and build the application container.
                Therefore, if image_name is not specified, there should
                be a Dockerfile in the correct location:

                Non-java apps (apps that use .yaml files)
                1) The .yaml file must be in the root of the app
                   directory.
                2) The Dockerfile must be in the root of the app
                   directory.

                Java apps (apps that are built off java-compat):
                1) The appengine-web.xml file must be in
                   <root>/WEB-INF/ (where <root> is the root
                   directory of the WAR archive.)
                2) The Dockerfile must be in the root of the WAR
                   archive.
                3) There must be a web.xml file in the same
                   directory as the appengine-web.xml file.
            admin_port: (int) The port on the docker server host that
                should be mapped to the admin server, which runs inside
                the devappserver container. The admin panel will be
                accessible through this port.
            application_id: (basestring) The application ID is
                the unique "appengine application ID" that the app is
                identified by, and can be found in the developer's
                console. While for deployment purposes, this ID is
                important, it's not as important in development. This
                ID only controls which datastore, blobstore, etc the
                sandbox will use. If the sandbox is run consecutively
                with the same application_id, (and of course, the same
                storage_path) the datastore, blobstore, taskqueue, etc
                will persist assuming their data has not been deleted.
            app_port: (int) The port on the docker host that should be
                mapped to the application. The application will be
                accessible through this port.
            image_name: (basestring or None) If specified, the sandbox
                will run the image associated with image_name instead of
                building an image from the specified application_directory.
            internal_admin_port: (int) The port INSIDE the devappserver
                container that the admin panel binds to. Because this
                is internal to the container, it can be defaulted.
                In fact, you shouldn't change it from the default unless
                you have a reason to.
            internal_api_port: (int) The port INSIDE the devappserver
                container that the api server should bind to.
                ~Same disclaimer as the one for internal_admin_port.~
            internal_proxy_port: (int) The port INSIDE the devappserver
                container that the proxy should bind to.
                ~Same disclaimer as the one for internal_admin_port.~
            log_path: (basetring) The path where the application's
                logs should be collected. Note that the application's logs
                will be collected EXTERNALLY (ie they will collect in the
                docker host's file system) and log_path specifies where
                these logs should go.
            run_api_server: (bool) Whether or not to run the api server.
                If this argument is set to false, the sandbox won't start
                a devappserver.
            storage_path: (basestring) The path (external to the
                containers) where the data associated with the api
                server's services - datastore, blobstore, etc - should
                collect. Note that this path defaults to
                /tmp/appengine/storage, so it should be changed if the data
                is intended to persist.
            use_cache: (bool) Whether or not to use the cache when building
                images.
        """
        self.devappserver_container = {}
        self.app_container = {}
        self.app_id = application_id
        self.internal_api_port = internal_api_port
        self.internal_proxy_port = internal_proxy_port
        self.internal_admin_port = internal_admin_port
        self.port = app_port
        self.storage_path = storage_path
        self.log_path = log_path
        self.image_name = image_name
        self.admin_port = admin_port
        self.dclient = utils.get_docker_client()
        self.nocache = not use_cache
        self.run_devappserver = run_api_server
        self.conf_path = os.path.abspath(config_file)
        self.verify_structure(self.conf_path)
        self.app_dir = (self.app_directory_from_config(self.conf_path)
                        if not image_name else None)

        self.conf_name = os.path.basename(self.conf_path)
        self.cur_time = time.strftime(TIME_FMT)

        # For Java apps, the xml file must be offset by WEB-INF.
        # Otherwise, devappserver will think that it's a non-java app.
        is_java_app = self.conf_name.endswith('.xml')
        self.internal_offset = JAVA_OFFSET if is_java_app else ''

    def __enter__(self):
        self.start()
        return self

    def start(self):
        """Start the sandbox."""
        try:
            self.create_and_run_containers()
        except KeyboardInterrupt:  # pylint: disable=bare-except
            self.stop()
            get_logger().warning('Caught SIGINT when the sandbox '
                                 'was being set up. The environment was '
                                 'successfully cleaned up.')
            sys.exit(0)
        except:
            self.stop()
            get_logger().warning('An error was detected when the sandbox '
                                 'was being set up. The environment was '
                                 'successfully cleaned up.')
            raise

    def create_and_run_containers(self):
        """Creates and runs app and (optionally) devappserver containers.

        This includes the creation of a new devappserver image, unless
        self.run_devappserver is False. If image_name isn't specified, an
        image is created for the application as well. Newly made containers
        are cleaned up, but newly made images are not.
        """

        if self.run_devappserver:
            # Devappserver must know APP_ID to properly interface with
            # services like datastore, blobstore, etc. It also needs
            # to know where to find the config file, which port to
            # run the proxy on, and which port to run the api server on.
            das_env = {'APP_ID': self.app_id,
                       'PROXY_PORT': self.internal_proxy_port,
                       'API_PORT': self.internal_api_port,
                       'APP_YAML_FILE': os.path.join(self.internal_offset,
                                                     self.conf_name)}
            devappserver_image = self.build_devappserver_image()
            devappserver_container_name = (
                self.make_timestamped_name('devappserver',
                                           self.cur_time))

            # The host_config specifies port bindings and volume bindings.
            # /storage is bound to the storage_path. Internally, the
            # devappserver writes all the db files to /storage. The mapping
            # thus allows these files to appear on the host machine. As for
            # port mappings, we only want to expose the application (via the
            # proxy), and the admin panel.
            devappserver_hconf = docker.utils.create_host_config(
                port_bindings={
                    self.internal_proxy_port: self.port,
                    self.internal_admin_port: self.admin_port,
                },
                binds={
                    self.storage_path: {'bind': '/storage'},
                }
            )

            self.devappserver_container = self.dclient.create_container(
                name=devappserver_container_name,
                image=devappserver_image,
                ports=[self.internal_proxy_port, self.internal_admin_port],
                volumes=['/storage'],
                host_config=devappserver_hconf,
                environment=das_env
            )

            self.dclient.start(self.devappserver_container.get('Id'))
            get_logger().info('Starting container: %s',
                              devappserver_container_name)

        # The application container needs several environment variables
        # in order to start up the application properly, as well as
        # look for the api server in the correct place. Notes:
        #
        # GAE_PARTITION is always dev for development modules.
        # GAE_LONG_APP_ID is the "application ID". When devappserver
        #     is invoked, it can be passed a "--application" flag. This
        #     application must be consistent with GAE_LONG_APP_ID.
        # API_HOST is 0.0.0.0 because application container runs on the
        #     same network stack as devappserver.
        # MODULE_YAML_PATH specifies the path to the app from the
        #     app directory
        app_env = {'API_HOST': '0.0.0.0',
                   'API_PORT': self.internal_api_port,
                   'GAE_LONG_APP_ID': self.app_id,
                   'GAE_PARTITION': 'dev',
                   'GAE_MODULE_INSTANCE': '0',
                   'MODULE_YAML_PATH': os.path.basename(self.conf_path),
                   'GAE_MODULE_NAME': 'default',
                   'GAE_MODULE_VERSION': '1',
                   'GAE_SERVER_PORT': '8080',
                   'USE_MVM_AGENT': 'true'}

        # Build from the application directory iff image_name is not
        # specified.
        app_image = self.image_name or self.build_app_image()
        app_container_name = self.make_timestamped_name('test_app',
                                                        self.cur_time)

        # If devappserver is running, hook up the app to it.
        if self.run_devappserver:
            ports = [DEFAULT_APPLICATION_PORT]
            port_bindings = {DEFAULT_APPLICATION_PORT: self.port}
            network_mode = ('container:%s' %
                            self.devappserver_container.get('Id'))
        else:
            ports = port_bindings = network_mode = None

        app_hconf = docker.utils.create_host_config(
            port_bindings=port_bindings,
            binds={
                self.log_path: {'bind': '/var/log/app_engine'}
            },

        )

        self.app_container = self.dclient.create_container(
            name=app_container_name,
            image=app_image,
            ports=ports,
            volumes=['/var/log/app_engine'],
            host_config=app_hconf,
            environment=app_env,
        )

        # Start as a shared network container, putting the application
        # on devappserver's network stack. (If devappserver is not
        # running, network_mode is None).
        self.dclient.start(self.app_container.get('Id'),
                           network_mode=network_mode)

        get_logger().info('Starting container: %s', app_container_name)
        self.wait_for_start()
        get_logger().info('Your application is live. Access it at: %s:%s',
                          self.get_docker_host(),
                          str(self.port))

    def stop(self):
        """Remove containers to clean up the environment."""
        self.stop_and_remove_containers()

    def __exit__(self, etype, value, traceback):
        """Stop and remove containers to clean up the environment.

        Args:
            etype: (type) The type of exception
            value: (Exception) An instance of the exception raised
            traceback: (traceback) An instance of the current traceback

        Returns:
            True if the sandbox was exited normally (ie exiting the with
            block or KeyboardInterrupt).
        """
        self.stop()

    def stop_and_remove_containers(self):
        """Stop and remove application containers."""
        for cont in [self.devappserver_container, self.app_container]:
            if 'Id' in cont:
                cont_id = cont.get('Id')
                if cont_id is None:
                    continue
                get_logger().info('Stopping %s', cont_id)
                self.dclient.kill(cont_id)

                get_logger().info('Removing %s', cont_id)
                cont['Id'] = None
                self.dclient.remove_container(cont_id)

    def get_docker_host(self):
        """Get hostname of machine where the docker client is running.

        Returns:
            (basestring) The hostname of the machine where the docker
            client is running.
        """
        return urlparse.urlparse(self.dclient.base_url).hostname

    def wait_for_start(self):
        """Wait for the app container to start.

        Raises:
            RuntimeError: If the application server doesn't
                start after MAX_ATTEMPTS to reach it on 8080.
        """
        attempt = 1
        while True:
            get_logger().info('Checking if server running: attempt #%i',
                              attempt)
            try:
                requests.get('http://%s:%s/' %
                             (self.get_docker_host(), self.port))
                break
            except requests.exceptions.ConnectionError:
                pass

            attempt += 1
            if attempt > MAX_ATTEMPTS:
                raise RuntimeError('The application server timed out.')
            time.sleep(1)

    def build_app_image(self):
        """Build the app image from the Dockerfile in app_dir.

        Returns:
            (basestring) The name of the new app image.
        """
        name = self.make_timestamped_name('app_image', self.cur_time)
        res = self.dclient.build(path=self.app_dir,
                                 rm=True,
                                 nocache=self.nocache,
                                 quiet=False,
                                 tag=name)
        utils.log_and_check_build_results(res, name)
        return name

    def build_devappserver_image(self):
        """Build a layer over devappserver to include application files.

        The new image contains the user's config files.

        Returns:
            (basestring) The name of the new devappserver image.
        """
        # pylint: disable=too-many-locals, unused-variable
        # Collect the files that should be added to the docker build
        # context.
        files_to_add = {self.conf_path: None}
        if self.conf_path.endswith('.xml'):
            files_to_add[self.get_web_xml(self.conf_path)] = None

        # The Dockerfile should add the config files to
        # the /app folder in devappserver's container.
        dockerfile = """
        FROM %(das_repo)s
        ADD %(path)s/* %(dest)s
        """ %{'das_repo': DEVAPPSERVER_IMAGE,
              'path': os.path.dirname(self.conf_path),
              'dest': os.path.join('/app', self.internal_offset)}

        # Construct a file-like object from the Dockerfile.
        dockerfile_obj = io.BytesIO(dockerfile.encode('utf-8'))
        build_context = utils.make_tar_build_context(dockerfile_obj,
                                                     files_to_add)
        image_name = self.make_timestamped_name('devappserver_image',
                                                self.cur_time)

        # Build the devappserver image.
        res = self.dclient.build(fileobj=build_context,
                                 custom_context=True,
                                 rm=True,
                                 nocache=self.nocache,
                                 tag=image_name)

        # Log the output of the build.
        utils.log_and_check_build_results(res, image_name)
        return image_name

    @staticmethod
    def get_web_xml(full_config_file_path):
        """Get (what should be) the path of the web.xml file.

        Args:
            full_config_file_path: (basestring) The absolute path to a
                .xml config file.

        Returns:
            (basestring) The full path to the web.xml file.
        """
        return os.path.join(os.path.dirname(full_config_file_path),
                            'web.xml')

    @staticmethod
    def verify_structure(full_config_file_path):
        """Verify the correctness of the configuration files.

        This includes making sure that the configuration file exists and
        has a proper extension. If the config file is an xml file, there
        needs to be a web.xml file in the same directory.

        Args:
            full_config_file_path: (basestring) The absolute path to a
                .xml or .yaml config file.

        Raises:
            ValueError: If the config_file does not have a proper
                extension (.yaml or .xml)
            IOError: If the application is a Java app, and
                the web.xml file cannot be found, or the config
                file cannot be found.
        """
        if not os.path.exists(full_config_file_path):
            raise IOError('The path %s could not be resolved.' %
                          full_config_file_path)

        filename = os.path.basename(full_config_file_path)
        if not (filename.endswith('.xml') or filename.endswith('.yaml')):
            raise ValueError('config_file is not a valid '
                             'configuration file. Use either a .yaml '
                             'file or .xml file.')

        if filename.endswith('.xml'):
            webxml = ContainerSandbox.get_web_xml(full_config_file_path)
            if not os.path.exists(webxml):
                raise IOError('Could not find web.xml at: %s' % webxml)

    @staticmethod
    def app_directory_from_config(full_config_file_path):
        """Get the application root directory based on the config file.

        Args:
            full_config_file_path: (basestring) The absolute path to a
                config file.

        Returns:
            (basestring): The application's root directory.
        """
        conf_file_dir = os.path.dirname(full_config_file_path)
        if full_config_file_path.endswith('.yaml'):
            return conf_file_dir
        else:
            return os.path.dirname(conf_file_dir)

    @staticmethod
    def make_timestamped_name(base, time_str):
        """Construct a name for an image or container.

        Note that naming is functionally unimportant and
        serves only to make the output of 'docker images'
        and 'docker ps' look cleaner.

        Args:
            base: (basestring) The prefix of the name.
            time_str: (basestring) The name's timestamp.
        Returns:
            (basestring) The name of the image or container.
        """
        return '%s.%s' % (base, time_str)
