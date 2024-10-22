import os
import pathlib
import yaml
import onnx
import urllib.request
from urllib.parse import urlparse

from PyQt5.QtCore import QCoreApplication

import ssl

ssl._create_default_https_context = (
    ssl._create_unverified_context
)  # Prevent issue when downloading models behind a proxy

import socket

socket.setdefaulttimeout(240)  # Prevent timeout when downloading models

from abc import abstractmethod


from PyQt5.QtCore import QFile, QObject
from PyQt5.QtGui import QImage

from .types import AutoLabelingResult
from anylabeling.views.labeling.logger import logger
from anylabeling.views.labeling.label_file import LabelFile, LabelFileError


class Model(QObject):
    BASE_DOWNLOAD_URL = (
        "https://github.com/CVHub520/X-AnyLabeling/releases/tag"
    )
    home_dir = os.path.expanduser("E:\models\yanglao")
    class Meta(QObject):
        required_config_names = []
        widgets = ["button_run"]
        output_modes = {
            "rectangle": QCoreApplication.translate("Model", "Rectangle"),
        }
        default_output_mode = "rectangle"

    def __init__(self, model_config, on_message) -> None:
        super().__init__()
        self.on_message = on_message
        # Load and check config
        if isinstance(model_config, str):
            if not os.path.isfile(model_config):
                raise FileNotFoundError(
                    QCoreApplication.translate(
                        "Model", "Config file not found: {model_config}"
                    ).format(model_config=model_config)
                )
            with open(model_config, "r") as f:
                self.config = yaml.safe_load(f)
        elif isinstance(model_config, dict):
            self.config = model_config
        else:
            raise ValueError(
                QCoreApplication.translate(
                    "Model", "Unknown config type: {type}"
                ).format(type=type(model_config))
            )
        self.check_missing_config(
            config_names=self.Meta.required_config_names,
            config=self.config,
        )
        self.output_mode = self.Meta.default_output_mode

    def get_required_widgets(self):
        """
        Get required widgets for showing in UI
        """
        return self.Meta.widgets

    @staticmethod
    def allow_migrate_data():
        home_dir = os.path.expanduser("~")
        old_model_path = os.path.join(home_dir, "anylabeling_data")
        new_model_path = os.path.join(home_dir, "xanylabeling_data")

        if os.path.exists(new_model_path) or not os.path.exists(
            old_model_path
        ):
            return True

        # Check if the current env have write permissions
        if not os.access(home_dir, os.W_OK):
            return False

        # Attempt to migrate data
        try:
            os.rename(old_model_path, new_model_path)
            return True
        except Exception as e:
            logger.error(f"An error occurred during data migration: {str(e)}")
            return False

    def get_model_abs_path(self, model_config, model_path_field_name):
        """
        Get model absolute path from config path or download from url
        """
        # Try getting model path from config folder
        model_path = model_config[model_path_field_name]
        local = model_path.get("local", None)
        online = model_path.get("online", None)
        # Model path is a local path
        if local is not None:
            # Relative path to executable or absolute path?
            model_abs_path = os.path.abspath(local)
            if os.path.exists(model_abs_path):
                return model_abs_path

            # Relative path to config file?
            config_file_path = model_config["config_file"]
            config_folder = os.path.dirname(config_file_path)
            model_abs_path = os.path.abspath(
                os.path.join(config_folder, local)
            )
            print(model_abs_path)
            if os.path.exists(model_abs_path):
                return model_abs_path

            self.on_message("Model path not found: {model_path}".format(model_path=local))

        # Download model from url
        self.on_message("Downloading model from registry...")

        # Build download url
        def get_filename_from_url(url):
            a = urlparse(url)
            return os.path.basename(a.path)

        filename = get_filename_from_url(online)
        download_url = online

        # Continue with the rest of your function logic
        migrate_flag = self.allow_migrate_data()
        data_dir = "xanylabeling_data" if migrate_flag else "anylabeling_data"

        # Create model folder
        model_path = os.path.abspath(os.path.join(self.home_dir, data_dir))
        model_abs_path = os.path.abspath(
            os.path.join(
                model_path,
                "flows",
                model_config["name"],
                filename,
            )
        )
        if os.path.exists(model_abs_path):
            if model_abs_path.lower().endswith(".onnx"):
                try:
                    onnx.checker.check_model(model_abs_path)
                except onnx.checker.ValidationError as e:
                    logger.error(f"{str(e)}")
                    logger.warning("Action: Delete and redownload...")
                    try:
                        os.remove(model_abs_path)
                    except Exception as e:  # noqa
                        logger.error(f"Could not delete: {str(e)}")
                else:
                    return model_abs_path
            else:
                return model_abs_path
        pathlib.Path(model_abs_path).parent.mkdir(parents=True, exist_ok=True)

        # Download url
        ellipsis_download_url = download_url
        if len(download_url) > 40:
            ellipsis_download_url = (
                    download_url[:20] + "..." + download_url[-20:]
            )
        logger.info(f"Downloading {ellipsis_download_url} to {model_abs_path}")
        try:
            # Download and show progress
            def _progress(count, block_size, total_size):
                percent = int(count * block_size * 100 / total_size)
                self.on_message(
                    "Downloading {download_url}: {percent}%".format(
                        download_url=ellipsis_download_url, percent=percent
                    )
                )

            urllib.request.urlretrieve(
                download_url, model_abs_path, reporthook=_progress
            )
        except Exception as e:  # noqa
            logger.error(f"Could not download {download_url}: {e}")
            self.on_message(f"Could not download {download_url}")
            return None

        return model_abs_path

    def check_missing_config(self, config_names, config):
        """
        Check if config has all required config names
        """
        for name in config_names:
            if name not in config:
                raise Exception(f"Missing config: {name}")

    @abstractmethod
    def predict_shapes(self, image, filename=None) -> AutoLabelingResult:
        """
        Predict image and return AnyLabeling shapes
        """
        raise NotImplementedError

    @abstractmethod
    def unload(self):
        """
        Unload memory
        """
        raise NotImplementedError

    @staticmethod
    def load_image_from_filename(filename):  #确保转成标准类型
        """Load image from labeling file and return image data and image path."""
        label_file = os.path.splitext(filename)[0] + ".json"
        if QFile.exists(label_file) and LabelFile.is_label_file(label_file):
            try:
                label_file = LabelFile(label_file)
            except LabelFileError as e:
                logger.error("Error reading {}: {}".format(label_file, e))
                return None, None
            image_data = label_file.image_data
        else:
            image_data = LabelFile.load_image_file(filename)
        image = QImage.fromData(image_data)
        if image.isNull():
            logger.error("Error reading {}".format(filename))
        return image

    def on_next_files_changed(self, next_files):
        """
        Handle next files changed. This function can preload next files
        and run inference to save time for user.
        """
        pass

    def set_output_mode(self, mode):
        """
        Set output mode
        """
        self.output_mode = mode
