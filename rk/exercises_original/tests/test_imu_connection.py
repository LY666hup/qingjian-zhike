import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import ai_fitness_common as common


class ImuBluetoothConnectionTests(unittest.TestCase):
    def test_connected_requires_bluez_connected_yes(self):
        result = SimpleNamespace(
            returncode=0,
            stdout="Device AA:BB:CC\n\tConnected: yes\n",
        )
        with patch.object(common.subprocess, "run", return_value=result):
            self.assertTrue(common._is_imu_bluetooth_connected())

    def test_local_binding_without_remote_connection_is_not_connected(self):
        result = SimpleNamespace(
            returncode=0,
            stdout="Device AA:BB:CC\n\tConnected: no\n",
        )
        with patch.object(common.subprocess, "run", return_value=result):
            self.assertFalse(common._is_imu_bluetooth_connected())

    def test_bluetoothctl_failure_is_not_connected(self):
        with patch.object(
            common.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("bluetoothctl", 5),
        ):
            self.assertFalse(common._is_imu_bluetooth_connected())

    def test_pair_removes_old_bond_and_requires_new_bond(self):
        commands = []

        def run(command, **kwargs):
            commands.append(command)
            if "pair" in command:
                return SimpleNamespace(
                    returncode=0,
                    stdout="Pairing successful\n",
                    stderr="",
                )
            if command[:2] == ["bluetoothctl", "info"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        "\tPaired: yes\n"
                        "\tBonded: yes\n"
                        "\tTrusted: yes\n"
                    ),
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        agent = object()
        scanner = object()
        with (
            patch.object(common.subprocess, "run", side_effect=run),
            patch.object(
                common, "_start_imu_scan", return_value=scanner
            ) as start_scan,
            patch.object(common, "_stop_imu_scan") as stop_scan,
            patch.object(
                common, "_start_imu_pairing_agent", return_value=agent
            ) as start_agent,
            patch.object(
                common, "_wait_for_imu_band_discovery", return_value=True
            ),
            patch.object(common, "_stop_imu_pairing_agent") as stop_agent,
        ):
            self.assertTrue(common._pair_imu_band())

        start_scan.assert_called_once_with()
        stop_scan.assert_called_once_with(scanner)
        start_agent.assert_called_once_with()
        stop_agent.assert_called_once_with(agent)

        self.assertIn(
            ["bluetoothctl", "remove", common.IMU_BAND_ADDRESS],
            commands,
        )
        pair_command = next(command for command in commands if "pair" in command)
        self.assertEqual(
            pair_command,
            [
                "bluetoothctl", "--timeout", "25", "pair",
                common.IMU_BAND_ADDRESS,
            ],
        )
        self.assertIn(
            ["bluetoothctl", "trust", common.IMU_BAND_ADDRESS],
            commands,
        )

    def test_pair_rejects_missing_bond(self):
        def run(command, **kwargs):
            if "pair" in command:
                return SimpleNamespace(
                    returncode=0,
                    stdout="Pairing successful\n",
                    stderr="",
                )
            if command[:2] == ["bluetoothctl", "info"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        "\tPaired: yes\n"
                        "\tBonded: no\n"
                        "\tTrusted: yes\n"
                    ),
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch.object(common.subprocess, "run", side_effect=run),
            patch.object(common, "_start_imu_scan", return_value=object()),
            patch.object(common, "_stop_imu_scan"),
            patch.object(
                common, "_start_imu_pairing_agent", return_value=object()
            ),
            patch.object(
                common, "_wait_for_imu_band_discovery", return_value=True
            ),
            patch.object(common, "_stop_imu_pairing_agent"),
        ):
            self.assertFalse(common._pair_imu_band())

    def test_pair_timeout_returns_false(self):
        with patch.object(
            common.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("bluetoothctl", 30),
        ):
            self.assertFalse(common._pair_imu_band())

    def test_pair_does_not_run_before_band_is_rediscovered(self):
        commands = []

        def run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        scanner = object()
        with (
            patch.object(common.subprocess, "run", side_effect=run),
            patch.object(common, "_start_imu_scan", return_value=scanner),
            patch.object(common, "_stop_imu_scan") as stop_scan,
            patch.object(
                common, "_wait_for_imu_band_discovery", return_value=False
            ),
            patch.object(common, "_start_imu_pairing_agent") as start_agent,
        ):
            self.assertFalse(common._pair_imu_band())

        self.assertFalse(any("pair" in command for command in commands))
        start_agent.assert_not_called()
        stop_scan.assert_called_once_with(scanner)


if __name__ == "__main__":
    unittest.main()
