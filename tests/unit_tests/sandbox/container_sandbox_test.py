# Copyright 2015 Google Inc. All Rights Reserved.
"""Unit tests for ContainerSandbox."""
# This file conforms to the external style guide
# pylint: disable=bad-indentation, g-bad-import-order

import logging
import os
import tempfile
import unittest

import docker
import mox

from appstart import sandbox
from appstart import utils

from fakes import fake_docker


class TestBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        test_directory = tempfile.mkdtemp()
        app_yaml = 'vm: true'
        cls.conf_file = open(os.path.join(test_directory, 'app.yaml'), 'w')
        cls.conf_file.write(app_yaml)
        cls.conf_file.close()

    def setUp(self):
        self.old_docker_client = docker.Client
        docker.Client = fake_docker.FakeDockerClient
        self.mocker = mox.Mox()
        fake_docker.reset()

    def tearDown(self):
        """Restore docker.Client and requests.get."""
        self.mocker.VerifyAll()
        self.mocker.UnsetStubs()
        docker.Client = self.old_docker_client


# pylint: disable=too-many-public-methods
class CreateAndRemoveContainersTest(TestBase):
    """Test the full code paths associated with starting the sandbox."""

    def setUp(self):
        super(CreateAndRemoveContainersTest, self).setUp()
        self.old_ping = (
            sandbox.container.PingerContainer.ping_application_container)

        # Fake out ping. Under the hood, this is a docker exec.
        sandbox.container.PingerContainer.ping_application_container = (
            lambda self: True)

        # Fake out stream_logs, as this will try to start another thread.
        self.old_logs = sandbox.container.Container.stream_logs
        sandbox.container.Container.stream_logs = (
            lambda unused_self, unused_stream=True: None)

    def test_start_from_conf(self):
        """Test ContainerSandbox.start."""
        sb = sandbox.container_sandbox.ContainerSandbox(self.conf_file.name)
        sb.start()

        self.assertIsNotNone(sb.app_container)
        self.assertIsNotNone(sb.devappserver_container)
        self.assertIsNotNone(sb.app_container)

    def test_start_no_api_server(self):
        """Test ContainerSandbox.start (with no api server)."""
        sb = sandbox.container_sandbox.ContainerSandbox(self.conf_file.name,
                                                        run_api_server=False)
        sb.start()
        self.assertIsNotNone(sb.app_container)
        self.assertIsNotNone(sb.app_container)
        self.assertIsNone(sb.devappserver_container)

    def test_start_from_image(self):
        sb = sandbox.container_sandbox.ContainerSandbox(image_name='test_image')
        with self.assertRaises(utils.AppstartAbort):
            sb.start()

        fake_docker.reset()
        fake_docker.images.append('test_image')
        sb.start()

        self.assertEqual(len(fake_docker.images),
                         len(fake_docker.DEFAULT_IMAGES) + 2,
                         'Too many images created')

    def test_start_no_image_no_conf(self):
        with self.assertRaises(utils.AppstartAbort):
            sandbox.container_sandbox.ContainerSandbox()

    def tearDown(self):
        super(CreateAndRemoveContainersTest, self).tearDown()

        # Reset everything
        sandbox.container.PingerContainer.ping_application_container = (
            self.old_ping)
        sandbox.container.Container.stream_logs = self.old_logs


class BadVersionTest(unittest.TestCase):

    def setUp(self):
        fake_docker.reset()

    def test_bad_version(self):
        """Test ContainerSandbox.create_and_run_containers.

        With a bad version, construction of the sandbox should fail.
        """
        docker.Client.version = lambda _: {'Version': '1.6.0'}
        with self.assertRaises(utils.AppstartAbort):
            sandbox.container_sandbox.ContainerSandbox(image_name='temp')


class ExitTest(TestBase):
    """Ensure the ContainerSandbox exits properly."""

    def setUp(self):
        """Populate the sandbox fake containers.

        This simulates the scenario where create_and_run_containers() has
        just run successfully.
        """
        super(ExitTest, self).setUp()
        self.sandbox = sandbox.container_sandbox.ContainerSandbox(
            self.conf_file.name)
        # Add the containers to the sandbox. Mock them out (we've tested the
        # containers elsewhere, and we just need the appropriate methods to be
        # called).
        self.sandbox.app_container = (
            self.mocker.CreateMock(sandbox.container.ApplicationContainer))

        self.sandbox.devappserver_container = (
            self.mocker.CreateMock(sandbox.container.Container))

        self.sandbox.pinger_container = (
            self.mocker.CreateMock(sandbox.container.PingerContainer))

        # TODO(gouzenko): Figure out how to make order not matter (among the
        # three containers).
        self.sandbox.app_container.running().AndReturn(True)
        self.sandbox.app_container.get_id().AndReturn('456')
        self.sandbox.app_container.kill()
        self.sandbox.app_container.remove()

        self.sandbox.devappserver_container.running().AndReturn(True)
        self.sandbox.devappserver_container.get_id().AndReturn('123')
        self.sandbox.devappserver_container.kill()
        self.sandbox.devappserver_container.remove()

        self.sandbox.pinger_container.running().AndReturn(True)
        self.sandbox.pinger_container.get_id().AndReturn('123')
        self.sandbox.pinger_container.kill()
        self.sandbox.pinger_container.remove()
        self.mocker.ReplayAll()

    def test_stop(self):
        self.sandbox.stop()

    def test_exception_handling(self):
        """Test the case where an exception was raised in start().

        The sandbox should stop and remove all containers before
        re-raising the exception.
        """

        def excep_func():
            """Simulate arbitrary exception."""
            raise Exception

        self.sandbox.create_and_run_containers = excep_func

        with self.assertRaises(Exception):
            self.sandbox.start()


class StaticTest(unittest.TestCase):

    def setUp(self):
        self.sandbox = sandbox.container_sandbox.ContainerSandbox

    def test_get_web_xml(self):
        self.assertEqual(self.sandbox.get_web_xml('/conf/appengine-web.xml'),
                         '/conf/web.xml',
                         'web.xml must be in same folder as appengine-web.xml')

    def test_app_directory_from_config(self):
        self.assertEqual(
            self.sandbox.app_directory_from_config('/app/blah/app-web.xml'),
            '/app')

        self.assertEqual(
            self.sandbox.app_directory_from_config('/app/app.yaml'),
            '/app')

if __name__ == '__main__':
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main()
