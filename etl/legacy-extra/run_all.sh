#!/bin/bash
#############################################################
# run_all.sh — "orchestration"
#
# Runs the whole CUSTBILL chain end to end. Dependency
# management is a sleep: we assume each stage is done after
# 10 minutes. If it isn't, the next stage runs on partial
# data. This has been "good enough" since 2014.
#
# Set RUN_ALL_SLEEP=0 for demo/dev runs (added 2022 so the
# new hire could demo it without waiting 20 minutes).
#############################################################

DIR=`dirname $0`
SLEEP=${RUN_ALL_SLEEP:-600}

echo "`date` run_all starting (sleep=$SLEEP between stages)"

$DIR/jobs/sftp_ingest_poll.ksh 2>/dev/null || true
sleep $SLEEP   # "wait for ingest to finish"

$DIR/jobs/parse_custbill_fixedwidth.sh 2>/dev/null || true
sleep $SLEEP   # "wait for parse to finish"

$DIR/jobs/finance_excel_report.pl 2>/dev/null || true

echo "`date` run_all done (probably)"
exit 0
