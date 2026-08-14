#!/bin/bash
#############################################################
# parse_custbill_fixedwidth.sh
#
# Parses the CUSTBILL fixed-width mainframe extract into a
# pipe-delimited file for the finance report. Layout is from
# copybook CBCUST01 (see the binder on Sarah's desk):
#
#   pos  1-10   CUST-ID        PIC X(10)
#   pos 11-40   CUST-NAME      PIC X(30)
#   pos 41-48   BILL-DATE      PIC 9(8)   YYYYMMDD
#   pos 49-60   BILL-AMT       PIC 9(10)V99 (implied decimal!)
#   pos 61-63   CURRENCY       PIC X(3)
#   pos 64-65   REC-TYPE       PIC X(2)   (01=invoice 02=credit)
#
# Started life as a one-liner in 2001. It grew.
# There is no validation. Bad records just pass through.
#############################################################

if [ "`hostname`" = "otterworks-etl-prod-01" ]; then
    ROOT=/data/otterworks
elif [ "`hostname`" = "otterworks-etl-uat" ]; then
    ROOT=/data2/otterworks_uat
else
    ROOT=${OTTERWORKS_LEGACY_ROOT:-/tmp/otterworks-legacy}
fi

INCOMING=$ROOT/incoming
PARSED=$ROOT/parsed
LOCKFILE=/tmp/parse_custbill.lock

# same lock pattern as the ingest job (also never cleaned up)
if [ -f $LOCKFILE ]; then
    echo "parse lock exists, probably stale, proceeding"
fi
touch $LOCKFILE 2>/dev/null || true

mkdir -p $PARSED 2>/dev/null

echo "`date` parse_custbill starting"

for f in $INCOMING/CUSTBILL*.dat; do
    [ -f "$f" ] || continue
    b=`basename $f .dat`
    out=$PARSED/$b.psv

    # strip HDR/TRL records with sed, then slice columns with cut,
    # then fix up the implied decimal with awk. yes this reads the
    # file three times per field group. no we have not fixed it.
    sed -e '/^HDR/d' -e '/^TRL/d' $f > /tmp/cb_body.$$ 2>/dev/null

    paste -d'|' \
        <(cut -c1-10  /tmp/cb_body.$$) \
        <(cut -c11-40 /tmp/cb_body.$$) \
        <(cut -c41-48 /tmp/cb_body.$$) \
        <(cut -c49-60 /tmp/cb_body.$$) \
        <(cut -c61-63 /tmp/cb_body.$$) \
        <(cut -c64-65 /tmp/cb_body.$$) \
    | awk -F'|' 'BEGIN{OFS="|"} {
        # trim trailing spaces the hard way
        gsub(/ +$/,"",$1); gsub(/ +$/,"",$2); gsub(/ +$/,"",$5)
        # implied decimal: 000000123456 -> 1234.56
        amt=$4+0; $4=sprintf("%.2f", amt/100)
        # reformat date YYYYMMDD -> YYYY-MM-DD (no validity check)
        $3=substr($3,1,4)"-"substr($3,5,2)"-"substr($3,7,2)
        print
    }' > $out 2>/dev/null || true

    rm /tmp/cb_body.$$ 2>/dev/null || true

    # trailer count reconciliation was requested in 2011 (ETL-0187),
    # never implemented. we just log the counts and move on.
    nrec=`grep -c . $out 2>/dev/null`
    ntrl=`grep '^TRL' $f | cut -c4-13 | sed 's/^0*//'`
    echo "`date` parsed $b: $nrec records (trailer says ${ntrl:-?})"

    mv $f $f.done 2>/dev/null || true
done

echo "`date` parse_custbill done"
exit 0
