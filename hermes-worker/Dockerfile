FROM nousresearch/hermes-agent@sha256:e7a77d7f45ef3be254eb717fd619990849d46012c984d362b9f8bde2cb6080ae

LABEL org.opencontainers.image.title="MUMU isolated Hermes worker"

ENV PYTHONUNBUFFERED=1

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libreoffice-writer libreoffice-calc libreoffice-impress \
    poppler-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim \
    fonts-noto-cjk fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache \
    "markitdown[docx,pptx,xlsx,xls,pdf]==0.1.3" \
    python-docx==1.2.0 python-pptx==1.0.2 openpyxl==3.1.5 xlrd==2.0.2 \
    pypdf==5.9.0 pdfplumber==0.11.7 reportlab==4.4.3 Pillow==11.3.0 pytesseract==0.3.13 \
    pandas==2.2.3 numpy==2.1.3 pyarrow==18.1.0 matplotlib==3.9.3 \
    PyYAML==6.0.2 rich==13.9.4

RUN npm install --global --omit=dev pptxgenjs@4.0.1

COPY document_tool.py /opt/document-tools/document-tool
COPY browser-use-wrapper.sh /usr/local/bin/browser-use
RUN chmod 0755 /opt/document-tools/document-tool \
    /usr/local/bin/browser-use \
    && ln -sf /opt/document-tools/document-tool /usr/local/bin/document-tool
