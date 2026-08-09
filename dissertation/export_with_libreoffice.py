#!/usr/bin/env python3
"""Refresh Word fields/TOC in LibreOffice and export the authoritative PDF."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import uno
from com.sun.star.beans import PropertyValue


def prop(name, value):
    item = PropertyValue()
    item.Name = name
    item.Value = value
    return item


def wait_for_port(port: int) -> None:
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.1):
                return
        except OSError:
            time.sleep(.1)
    raise RuntimeError("LibreOffice UNO listener did not start")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: export_with_libreoffice.py SOFFICE FINAL_DOCX")
    soffice = Path(sys.argv[1]).resolve()
    final_docx = Path(sys.argv[2]).resolve()
    final_pdf = final_docx.with_suffix(".pdf")
    with tempfile.TemporaryDirectory(prefix="raptor-lite-lo-") as temp:
        port = 20851
        profile = Path(temp) / "profile"
        proc = subprocess.Popen([
            str(soffice), f"-env:UserInstallation=file://{profile}", "--headless", "--nologo", "--nodefault", "--norestore",
            f"--accept=socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            wait_for_port(port)
            local = uno.getComponentContext()
            resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
            context = resolver.resolve(f"uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext")
            desktop = context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
            document = desktop.loadComponentFromURL(uno.systemPathToFileUrl(str(final_docx)), "_blank", 0, (prop("Hidden", True),))
            if document is None:
                raise RuntimeError("LibreOffice could not open the dissertation")
            indexes = document.getDocumentIndexes()
            index_count = indexes.getCount()
            if index_count == 0:
                raise RuntimeError("The imported DOCX contains no updateable table of contents")
            for index in indexes:
                index.update()
            document.getTextFields().refresh()
            updated = Path(temp) / "final_dissertation.docx"
            document.storeAsURL(uno.systemPathToFileUrl(str(updated)), (prop("FilterName", "Office Open XML Text"), prop("Overwrite", True)))
            document.storeToURL(uno.systemPathToFileUrl(str(final_pdf)), (prop("FilterName", "writer_pdf_Export"), prop("Overwrite", True)))
            document.close(True)
            shutil.copyfile(updated, final_docx)
            print(f"indexes_updated={index_count}")
            print(f"docx={final_docx}")
            print(f"pdf={final_pdf}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()


if __name__ == "__main__":
    main()
