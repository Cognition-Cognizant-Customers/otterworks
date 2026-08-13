# Single container image shared by all five ETL Lambda functions.
# Terraform sets a per-function image_config command pointing at the
# pipeline's handler, e.g. otterworks_etl.analytics.handler.handler
FROM public.ecr.aws/lambda/python:3.12

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/otterworks_etl ${LAMBDA_TASK_ROOT}/otterworks_etl

CMD ["otterworks_etl.analytics.handler.handler"]
