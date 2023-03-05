# Salesforce writer

The component takes files from in/tables directory and insert/upsert/update/delete appropriate records. The input table
must have a header with field names, which has to exists in Salesforce. ID column is used to identify record which will
be updated. When inserting records all required fields has to be filled in.

The process can fail on any records (due to missing required field or too large string) and this specific record will
not be inserted/updated/upserted/deleted. You can specify whether you want the writer to output the errors to a table.
Everything else will finish with success. There is no way how to rollback this transaction, so you have to carefully
check the log each time. It is also great idea to include a column with external IDs and based on them do upsert later.
External IDs will also save you from duplicated records when running insert several times.

If you need you can set the fail on error parameter true, this will cause the job of a component to fail if 1 or more
records fail to be inserted/updated/upserted/deleted. It is however it is recommended to have a pipeline set up to
processes unsuccessful updates and not have the component fail.

**NOTE** The component processes all records on the input and outputs tables containing the failed records with reason
of failure. The table names are constructed as `{OBJECT_NAME}_{LOAD_TYPE}_unsuccessful`
e.g. `Contact_upsert_unsuccessful`

**Table of contents:**

[TOC]

# Configuration

## Authorization

- **User Name** - (REQ) your user name, when exporting data from sandbox don't forget to add .sandboxname at the end
- **Password** - (REQ) your password
- **Security Token** - (REQ) your security token, don't forget it is different for sandbox
- **sandbox** - (REQ) true when you want to push data to sandbox

## Row configuration

* object - (REQ) name of object you wish to perform the operation on
* upsertField - required when the operation is upsert
* operation - (REQ) specify the operation you wish to do. Insert/Upsert/Update/Delete are supported.
* serialMode - true if you wish to run the import in serial mode.
* replaceString - string to be replaced in column name for dot, so you can use that column as reference to other record
  via external id
* fail_on_error - if you want the job to fail on any error, set this to true and the job will fail if more than 0 errors
  occur during the execution. When unchecked, the component will continue on failure. In both cases an output table with
  unsuccessful records is saved. The table name will be constructed as `{OBJECT_NAME}_{LOAD_TYPE}_unsuccessful`
  e.g. `Contact_upsert_unsuccessful`

- when inserting you cannot specify ID field
- when upserting the upsertField parameter is required
- when updating the ID field in CSV file is required
- when deleting, keep in mind that Salesforce's recycle bin can take less records than you are trying to delete, so they
  will be hard deleted. Also the CSV file must contain only ID field



