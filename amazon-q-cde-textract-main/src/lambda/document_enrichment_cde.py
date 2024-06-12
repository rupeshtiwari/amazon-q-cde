import os
import json
import boto3
import logging
import mimetypes

from textractor import Textractor
from textractor.data.constants import TextractFeatures
from textractor.data.text_linearization_config import TextLinearizationConfig

os.environ["LD_LIBRARY_PATH"] = f"/opt/python/bin/:{os.environ['LD_LIBRARY_PATH']}"
os.environ["PATH"] = f"/opt/python/bin/:{os.environ['PATH']}"
region_name = os.environ.get('AWS_DEFAULT_REGION')
tmp_folder = "/tmp"

logger = logging.getLogger()
logger.setLevel(logging.INFO)
 
s3 = boto3.client('s3')
extractor = Textractor(region_name=region_name)

def download_file_and_get_mime_type(bucket_name, object_key):
    """
    Downloads the file into `/tmp` and checks the MIME Type of
    the file. Supported file types are PNG, JPG, TIF, and PDF
    """
    download_path = f'/tmp/{os.path.basename(object_key)}'
    s3.download_file(Bucket=bucket_name, 
                     Key=object_key, 
                     Filename=download_path)    
    mime_type, _ = mimetypes.guess_type(download_path)    
    supported_doc = mime_type in ['image/png','image/jpeg','image/tiff','application/pdf']
    file_extension = os.path.splitext(object_key)[1]
    return supported_doc, file_extension


def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event)}")
    logger.info(f"Region is: {region_name}")
    s3Bucket = event.get("s3Bucket")
    s3ObjectKey = event.get("s3ObjectKey")
    metadata = event.get("metadata")

    response_object = { "version" : "v0",
                       "s3ObjectKey": s3ObjectKey,
                       "metadataUpdates": metadata["attributes"]
                       }
    try:
        """
        Check the mime type of the document
        """
        supported_file, file_type = download_file_and_get_mime_type(bucket_name=s3Bucket, object_key=s3ObjectKey)
        s3_uri = f"s3://{s3Bucket}/{s3ObjectKey}"
        if supported_file:        
            """
            This performs an async StartDocumentAnalysis with following features
            - LAYOUT
            - TABLES
            - FORMS (KV Pairs)
            - SIGNATURES
            """
            output = ""
            document = extractor.start_document_analysis(
                file_source=s3_uri,
                save_image=False,
                features=[TextractFeatures.LAYOUT, 
                        TextractFeatures.TABLES, 
                        TextractFeatures.FORMS, 
                        TextractFeatures.SIGNATURES]
            )
        
            """
            We will leverage the `TextLinearizationConfig` object which has over 40 
            options to tailor the text linearization. In this case -
            - Titles will be markdown formatted and prefixed with "# "
            - Section headers will be markdown formatted and prefixed with "## "
            - Tables will be HTML formatted
            See https://aws-samples.github.io/amazon-textract-textractor/notebooks/document_linearization_to_markdown_or_html.html#
            for more
            """
            config = TextLinearizationConfig(
                hide_figure_layout=False,
                title_prefix="# ",
                section_header_prefix="## ",
                table_prefix="<table>",
                table_suffix="</table>",
                table_cell_header_prefix = "<th>",
                table_cell_header_suffix = "</th>",
                table_row_prefix="<tr>",
                table_row_suffix="</tr>",
                table_cell_prefix="<td>",
                table_cell_suffix="</td>",            
                hide_page_num_layout=True,
            )
        
            # output = document.get_text(config=config)
            for page in document.pages:
                output = output + f"============================ Start Page {page.page_num} ============================\n"
                output = output + page.get_text(config=config)
                output = output + f"============================ End Page {page.page_num} ============================\n\n"
            # All text in the text document is extracted and saved in ".txt" format that Amazon Q supports.        
            new_key = f"cde_output/layout/{os.path.splitext(os.path.basename(s3ObjectKey))[0]}.txt"
            # Each S3 Object is considered a single document. All the .txt files are stored in S3 data-source.
            s3.put_object(Bucket=s3Bucket,
                          Key=new_key,
                          Body=output.encode('utf-8'))
            
            """
            CDE Data contract: https://docs.aws.amazon.com/amazonq/latest/qbusiness-ug/cde-lambda-operations.html#cde-lambda-operations-data-contracts 
            Document Attributes: https://docs.aws.amazon.com/amazonq/latest/qbusiness-ug/doc-attributes.html#doc-attribute-types
            """
            response_object["s3ObjectKey"] = new_key
            response_object["metadataUpdates"] = [
                    {"name":"_source_uri", "value":{"stringValue": s3_uri}},
                    {"name":"_file_type", "value":{"stringValue": file_type}},
                    # Need to experiment with _document_body
                    # {"name":"_document_body", "value":{"stringValue": output.encode('utf-8')}},
                    # Custom meta data can be used to enhance the search capability in chat see https://docs.aws.amazon.com/amazonq/latest/qbusiness-ug/doc-attributes.html#mapped-doc-attribute-types
                    # {"name":"custom_key1", "value":{"stringValue": "some_data"}},
                    # {"name":"custom_key1", "value":{"stringValue": "some_data"}},
                ]
            logger.info(f"Rresponding to indexer with: {json.dumps(response_object)}")
            return response_object
        else:
            # Textract can't read the document so just return as is
            logger.info(f"Document is not a supported file type-PNG, JPG, TIF, PDF")
            return response_object
    except Exception as e:
        logger.error(f"Error in enrichment function: {str(e)}")
        return response_object