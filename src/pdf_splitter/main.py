"""Application entry point."""

import sys

from PyQt6.QtWidgets import QApplication

from pdf_splitter.app import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("PDF Horizontal Splitter")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
