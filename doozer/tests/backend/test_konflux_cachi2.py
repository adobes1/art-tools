from unittest import TestCase
from unittest.mock import MagicMock, patch

from artcommonlib.model import Missing
from doozerlib.backend.konflux_image_builder import KonfluxImageBuilder


class TestKonfluxCachi2(TestCase):
    @patch("doozerlib.backend.konflux_client.KonfluxClient.from_kubeconfig")
    def test_prefetch_1(self, mock_konflux_client_init):
        builder = KonfluxImageBuilder(MagicMock())
        metadata = MagicMock()
        metadata.is_cachi2_enabled.return_value = False
        metadata.is_lockfile_generation_enabled.return_value = False

        self.assertEqual(builder._prefetch(metadata=metadata), [])

    @patch("doozerlib.backend.konflux_client.KonfluxClient.from_kubeconfig")
    def test_prefetch_2(self, mock_konflux_client_init):
        builder = KonfluxImageBuilder(MagicMock())
        metadata = MagicMock()
        metadata.is_cachi2_enabled.return_value = False
        metadata.is_lockfile_generation_enabled.return_value = False
        metadata.config.content.source.pkg_managers = ["unknown"]

        self.assertEqual(builder._prefetch(metadata=metadata), [])

    @patch("doozerlib.backend.konflux_client.KonfluxClient.from_kubeconfig")
    def test_prefetch_3(self, mock_konflux_client_init):
        builder = KonfluxImageBuilder(MagicMock())
        metadata = MagicMock()
        metadata.is_cachi2_enabled.return_value = True
        metadata.is_lockfile_generation_enabled.return_value = False
        metadata.config.content.source.pkg_managers = ["gomod"]

        self.assertEqual(builder._prefetch(metadata=metadata), [{"type": "gomod", "path": "."}])

    @patch("doozerlib.backend.konflux_client.KonfluxClient.from_kubeconfig")
    def test_prefetch_4(self, mock_konflux_client_init):
        builder = KonfluxImageBuilder(MagicMock())
        metadata = MagicMock()
        metadata.is_cachi2_enabled.return_value = True
        metadata.is_lockfile_generation_enabled.return_value = False
        metadata.config.content.source.pkg_managers = ["gomod"]
        metadata.config.cachito.packages = {'gomod': [{'path': 'api'}]}

        self.assertEqual(builder._prefetch(metadata=metadata), [{"type": "gomod", "path": "api"}])

    @patch("doozerlib.backend.konflux_client.KonfluxClient.from_kubeconfig")
    def test_prefetch_5(self, mock_konflux_client_init):
        builder = KonfluxImageBuilder(MagicMock())
        metadata = MagicMock()
        metadata.is_cachi2_enabled.return_value = True
        metadata.is_lockfile_generation_enabled.return_value = False
        metadata.config.content.source.pkg_managers = ["gomod"]
        metadata.config.cachito.packages = {"gomod": [{"path": "."}, {"path": "api"}, {"path": "client/pkg"}]}

        self.assertEqual(
            builder._prefetch(metadata=metadata),
            [{"type": "gomod", "path": "."}, {"type": "gomod", "path": "api"}, {"type": "gomod", "path": "client/pkg"}],
        )

    @patch("doozerlib.backend.konflux_client.KonfluxClient.from_kubeconfig")
    def test_prefetch_6(self, mock_konflux_client_init):
        builder = KonfluxImageBuilder(MagicMock())
        metadata = MagicMock()
        metadata.is_cachi2_enabled.return_value = True
        metadata.is_lockfile_generation_enabled.return_value = False
        metadata.config.content.source.pkg_managers = ["npm", "gomod"]
        metadata.config.cachito.packages = {'npm': [{'path': 'web'}], 'gomod': [{'path': '.'}]}

        self.assertEqual(
            builder._prefetch(metadata=metadata), [{'type': 'gomod', 'path': '.'}, {'type': 'npm', 'path': 'web'}]
        )

    @patch("doozerlib.backend.konflux_client.KonfluxClient.from_kubeconfig")
    def test_prefetch_rpm_lockfile_enabled(self, mock_konflux_client_init):
        builder = KonfluxImageBuilder(MagicMock())
        metadata = MagicMock()
        metadata.is_cachi2_enabled.return_value = True
        metadata.is_lockfile_generation_enabled.return_value = True
        metadata.config.content.source.pkg_managers = ["gomod"]
        metadata.config.cachito.packages = {'gomod': [{'path': '.'}]}
        metadata.config.konflux.cachi2.lockfile.get.return_value = "."

        result = builder._prefetch(metadata=metadata)
        expected = [{'type': 'rpm', 'path': '.'}, {'type': 'gomod', 'path': '.'}]
        self.assertEqual(result, expected)

    @patch("doozerlib.backend.konflux_client.KonfluxClient.from_kubeconfig")
    def test_prefetch_rpm_lockfile_custom_path(self, mock_konflux_client_init):
        builder = KonfluxImageBuilder(MagicMock())
        metadata = MagicMock()
        metadata.is_cachi2_enabled.return_value = True
        metadata.is_lockfile_generation_enabled.return_value = True
        metadata.config.content.source.pkg_managers = ["npm"]
        metadata.config.cachito.packages = {'npm': [{'path': 'frontend'}]}
        metadata.config.konflux.cachi2.lockfile.get.return_value = "custom/path"

        result = builder._prefetch(metadata=metadata)
        expected = [{'type': 'rpm', 'path': 'custom/path'}, {'type': 'npm', 'path': 'frontend'}]
        self.assertEqual(result, expected)
