import csv
import logging

import requests
from keboola.component.base import ComponentBase
from keboola.component.exceptions import UserException
from retry import retry
from salesforce_bulk import CsvDictsAdapter, BulkApiError
from simple_salesforce.exceptions import SalesforceAuthenticationFailed

from salesforce.client import SalesforceClient

KEY_USERNAME = "username"
KEY_PASSWORD = "#password"
KEY_SECURITY_TOKEN = "#security_token"
KEY_SANDBOX = "sandbox"

KEY_OBJECT = "sf_object"
KEY_REPLACE_STRING = "replace_string"
KEY_OPERATION = "operation"
KEY_ASSIGNMENT_ID = "assignment_id"
KEY_UPSERT_FIELD_NAME = "upsert_field_name"
KEY_SERIAL_MODE = "serial_mode"
KEY_FAIL_ON_ERROR = "fail_on_error"

REQUIRED_PARAMETERS = [KEY_USERNAME, KEY_OBJECT, KEY_PASSWORD, KEY_SECURITY_TOKEN, KEY_OPERATION]
REQUIRED_IMAGE_PARS = []

BATCH_LIMIT = 2500
LOG_LIMIT = 15


class Component(ComponentBase):
    def __init__(self):
        super().__init__(required_parameters=REQUIRED_PARAMETERS,
                         required_image_parameters=REQUIRED_IMAGE_PARS)

    def run(self):
        params = self.configuration.parameters
        input_table = self.get_input_table()

        try:
            salesforce_client = self.login_to_salesforce(params)
        except SalesforceAuthenticationFailed as e:
            raise UserException("Authentication Failed : recheck your username, password, and security token ") from e

        sf_object = params.get(KEY_OBJECT)
        operation = params.get(KEY_OPERATION).lower()

        upsert_field_name = params.get(KEY_UPSERT_FIELD_NAME)
        if upsert_field_name:
            upsert_field_name = upsert_field_name.strip()

        assignement_id = params.get(KEY_ASSIGNMENT_ID)
        if assignement_id:
            assignement_id = assignement_id.strip()

        logging.info(f"Running {operation} operation with input table to the {sf_object} Salesforce object")

        concurrency = 'Serial' if params.get(KEY_SERIAL_MODE) else 'Parallel'

        replace_string = params.get(KEY_REPLACE_STRING)
        input_headers = input_table.columns
        if replace_string:
            input_headers = self.replace_headers(input_headers, replace_string)
        if upsert_field_name and upsert_field_name.strip() not in input_headers:
            raise UserException(
                f"Upsert field name {upsert_field_name} not in input table with headers {input_headers}")

        input_file_reader = self.get_input_file_reader(input_table, input_headers)

        if operation == "delete" and len(input_headers) != 1:
            raise UserException("Delete operation should only have one column with id, input table contains "
                                f"{len(input_headers)} columns")

        try:
            results = self.write_to_salesforce(input_file_reader, upsert_field_name, salesforce_client,
                                               sf_object, operation, concurrency, assignement_id)
        except BulkApiError as bulk_error:
            raise UserException(bulk_error) from bulk_error

        parsed_results, num_success, num_errors = self.parse_results(results)

        logging.info(
            f"All data written to salesforce, {operation}ed {num_success} records, {num_errors} errors occurred")

        if params.get(KEY_FAIL_ON_ERROR) and num_errors > 0:
            self.log_errors(parsed_results, input_table, input_headers)
            raise UserException(
                f"{num_errors} errors occurred, since fail on error has been selected, the job has failed.")
        elif num_errors > 0:
            self.write_unsuccessful(parsed_results, input_headers, sf_object, operation)
        else:
            logging.info("Process was successful")

    @retry(SalesforceAuthenticationFailed, tries=3, delay=5)
    def login_to_salesforce(self, params):
        return SalesforceClient(username=params.get(KEY_USERNAME),
                                password=params.get(KEY_PASSWORD),
                                security_token=params.get(KEY_SECURITY_TOKEN),
                                sandbox=params.get(KEY_SANDBOX))

    def get_input_table(self):
        input_tables = self.get_input_tables_definitions()
        if len(input_tables) == 0:
            raise UserException("No input table added. Please add an input table")
        elif len(input_tables) > 1:
            raise UserException("Too many input tables added. Please add only one input table")
        return input_tables[0]

    @staticmethod
    def replace_headers(input_headers, replace_string):
        input_headers = [header.replace(replace_string, ".") for header in input_headers]
        return input_headers

    @staticmethod
    def get_input_file_reader(input_table, input_headers):
        with open(input_table.full_path, mode='r') as in_file:
            reader = csv.DictReader(in_file, fieldnames=input_headers)
            for input_row in reader:
                if sorted(input_row.values()) == sorted(input_headers):
                    logging.debug("Skipping header")
                else:
                    yield input_row

    @staticmethod
    def get_chunks(generator, chunk_size):
        chunk = []
        for item in generator:
            if len(chunk) >= chunk_size:
                yield chunk
                chunk = [item]
            else:
                chunk.append(item)
        if chunk:
            yield chunk

    @staticmethod
    @retry(delay=10, tries=4, backoff=2, exceptions=requests.exceptions.ConnectionError)
    def get_job_result(salesforce_client, job, csv_iter):
        batch = salesforce_client.post_batch(job, csv_iter)
        salesforce_client.wait_for_batch(job, batch)
        salesforce_client.close_job(job)
        return salesforce_client.get_batch_results(batch)

    @staticmethod
    def parse_results(results):
        parsed_results = []
        num_errors = 0
        num_success = 0
        for result in results:
            parsed_results.append({"id": result.id, "success": result.success, "error": result.error})
            if result.success == "false":
                num_errors = num_errors + 1
            else:
                num_success = num_success + 1
        return parsed_results, num_success, num_errors

    def write_to_salesforce(self, input_file_reader, upsert_field_name, salesforce_client,
                            sf_object, operation, concurrency, assignement_id):
        results = []
        for i, chunk in enumerate(self.get_chunks(input_file_reader, BATCH_LIMIT)):
            logging.info(f"Processing chunk #{i}")
            job_result = self.process_job(upsert_field_name, salesforce_client, sf_object, operation, concurrency,
                                          assignement_id, chunk)
            results.extend(job_result)
        return results

    @retry(delay=10, tries=4, backoff=2, exceptions=BulkApiError)
    def process_job(self, upsert_field_name, salesforce_client, sf_object, operation, concurrency, assignement_id,
                    chunk):

        job = salesforce_client.create_job(sf_object, operation, external_id_name=upsert_field_name,
                                           contentType='CSV', concurrency=concurrency,
                                           assignement_id=assignement_id)

        csv_iter = CsvDictsAdapter(iter(chunk))
        return self.get_job_result(salesforce_client, job, csv_iter)

    def write_unsuccessful(self, parsed_results, input_headers, sf_object, operation):
        unsuccessful_table_name = "".join([sf_object, "_", operation, "_unsuccessful.csv"])
        logging.info(f"Saving errors to {unsuccessful_table_name}")
        fieldnames = input_headers.copy()
        fieldnames.append("error")
        unsuccessful_table = self.create_out_table_definition(name=unsuccessful_table_name, columns=fieldnames)
        with open(unsuccessful_table.full_path, 'w+', newline='') as out_table:
            writer = csv.DictWriter(out_table, fieldnames=fieldnames, lineterminator='\n', delimiter=',')
            in_file_reader = self.get_input_file_reader(self.get_input_table(), input_headers)
            for i, row in enumerate(in_file_reader):
                if parsed_results[i]["success"] == "false":
                    error_row = row
                    error_row["error"] = parsed_results[i]["error"]
                    writer.writerow(error_row)
        self.write_manifest(unsuccessful_table)

    def log_errors(self, parsed_results, input_table, input_headers):
        logging.warning(f"Logging first {LOG_LIMIT} errors")
        fieldnames = input_headers.copy()
        fieldnames.append("error")
        for i, row in enumerate(self.get_input_file_reader(input_table, input_headers)):
            if parsed_results[i]["success"] == "false":
                error_row = row
                error_row["error"] = parsed_results[i]["error"]
                logging.warning(f"Failed to update row : {error_row}")
            if i >= LOG_LIMIT - 1:
                break


if __name__ == "__main__":
    try:
        comp = Component()
        comp.run()
    except UserException as exc:
        logging.exception(exc)
        exit(1)
    except Exception as exc:
        logging.exception(exc)
        exit(2)
