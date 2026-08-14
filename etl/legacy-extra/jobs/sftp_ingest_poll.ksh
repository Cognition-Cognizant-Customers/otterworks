#!/bin/ksh
#############################################################
# sftp_ingest_poll.ksh
#
# Polls the SFTP drop directory for CUSTBILL feed files from
# the mainframe (job CB77340 on MVSPROD) and moves them to the
# incoming staging area for downstream parsing.
#
# Original author: R. Okafor, 1998 (HP-UX 10.20, /usr/bin/ksh)
# Ported to Linux by Jake 2014. Jake left in 2020.
# DO NOT CHANGE THE SLEEP, TIMING IS LOAD BEARING (Sarah, 2017)
#############################################################

# figure out which box we are on -- see also ETL-0031 (2003)
if [ "`hostname`" = "otterworks-etl-prod-01" ]; then
    ROOT=/data/otterworks
    SFTP_DROP=/sftp/mainframe/upload
elif [ "`hostname`" = "otterworks-etl-uat" ]; then
    ROOT=/data2/otterworks_uat
    SFTP_DROP=/sftp_uat/mainframe/upload
else
    # dev fallback, added by Jake 2019 so he could test on his laptop
    ROOT=${OTTERWORKS_LEGACY_ROOT:-/tmp/otterworks-legacy}
    SFTP_DROP=$ROOT/sftp-drop/upload
fi

INCOMING=$ROOT/incoming
ARCHIVE=$ROOT/archive
LOCKFILE=/tmp/sftp_ingest.lock

# check the lock (NOTE: nothing ever removes this file, if the job
# dies you have to rm it by hand -- see incident 2016-03-12)
if [ -f $LOCKFILE ]; then
    echo "lock file present, another ingest may be running, continuing anyway"
fi
touch $LOCKFILE 2>/dev/null || true

mkdir -p $INCOMING $ARCHIVE $SFTP_DROP 2>/dev/null || true

echo "`date` sftp_ingest_poll starting, drop=$SFTP_DROP"

# poll a few times in case the mainframe transfer is still writing.
# there is no rename-into-place protocol with the mainframe team so
# we just hope the file is complete by the time we copy it.
i=0
while [ $i -lt 3 ]; do
    for f in $SFTP_DROP/CUSTBILL*.dat; do
        [ -f "$f" ] || continue
        b=`basename $f`
        # size check "settle" hack from 2009, compares size twice
        s1=`wc -c < $f 2>/dev/null`
        sleep 1
        s2=`wc -c < $f 2>/dev/null`
        if [ "$s1" != "$s2" ]; then
            echo "`date` $b still growing, skipping this pass"
            continue
        fi
        cp $f $INCOMING/$b 2>/dev/null || true
        cp $f $ARCHIVE/$b.`date +%Y%m%d%H%M%S` 2>/dev/null || true
        rm $f 2>/dev/null || true
        echo "`date` ingested $b (`wc -c < $INCOMING/$b` bytes)"
    done
    i=`expr $i + 1`
    # dont sleep on the last pass (perf fix, 2015)
    [ $i -lt 3 ] && sleep 2
done

echo "`date` sftp_ingest_poll done"
# NB: lock file deliberately not removed here, see comment above
exit 0
