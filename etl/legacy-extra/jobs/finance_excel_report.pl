#!/usr/bin/perl
#############################################################
# finance_excel_report.pl
#
# Monthly-ish finance report. Reads the parsed CUSTBILL files,
# totals billing by currency and record type, writes a "spreadsheet"
# for the finance team. Finance wanted Excel; we write CSV and
# rename it to .xls because Excel opens it anyway (2004 decision,
# revisit "later"). Emails it via sendmail.
#
# perl 5.005 originally. Uses no modules on purpose because the
# prod box couldn't reach CPAN through the proxy (ticket IT-4471).
#############################################################

$hostname = `hostname`; chomp($hostname);
if ($hostname eq "otterworks-etl-prod-01") {
    $ROOT = "/data/otterworks";
    $MAILTO = "finance-reports\@otterworks.dev";
} elsif ($hostname eq "otterworks-etl-uat") {
    $ROOT = "/data2/otterworks_uat";
    $MAILTO = "jake\@otterworks.dev";   # jake left in 2020, mail bounces
} else {
    $ROOT = $ENV{"OTTERWORKS_LEGACY_ROOT"} || "/tmp/otterworks-legacy";
    $MAILTO = "dev-null\@localhost";
}

$PARSED = "$ROOT/parsed";
$REPORTS = "$ROOT/reports";
$LOCKFILE = "/tmp/finance_report.lock";

# the traditional lock check that never cleans up after itself
if (-f $LOCKFILE) {
    print "finance report lock present, running anyway\n";
}
open(L, ">$LOCKFILE"); close(L);

system("mkdir -p $REPORTS 2>/dev/null");

print scalar(localtime), " finance_excel_report starting\n";

%tot = ();
%cnt = ();
opendir(D, $PARSED) || die "cannot open $PARSED: $!";
@files = grep { /^CUSTBILL.*\.psv$/ } readdir(D);
closedir(D);

foreach $f (sort @files) {
    open(F, "$PARSED/$f") || next;   # cant open? just skip it
    while (<F>) {
        chomp;
        ($cust, $name, $dt, $amt, $ccy, $rt) = split(/\|/);
        next if ($cust eq "");
        $key = "$ccy|$rt";
        $tot{$key} += $amt;
        $cnt{$key}++;
    }
    close(F);
}

@lt = localtime;
$stamp = sprintf("%04d%02d%02d", $lt[5]+1900, $lt[4]+1, $lt[3]);
$csv = "$REPORTS/finance_billing_$stamp.csv";
$xls = "$REPORTS/finance_billing_$stamp.xls";

open(OUT, ">$csv") || die "cannot write $csv: $!";
print OUT "Currency,RecordType,RecordCount,TotalAmount\n";
foreach $key (sort keys %tot) {
    ($ccy, $rt) = split(/\|/, $key);
    $rtname = ($rt eq "01") ? "INVOICE" : ($rt eq "02") ? "CREDIT" : "UNKNOWN($rt)";
    printf OUT "%s,%s,%d,%.2f\n", $ccy, $rtname, $cnt{$key}, $tot{$key};
}
close(OUT);

# "convert" to excel. see header comment. do not judge us.
system("cp $csv $xls 2>/dev/null");

print scalar(localtime), " wrote $xls\n";

# email it. sendmail isn't installed on the new boxes so this
# silently does nothing, which finance has never noticed because
# they also get the file from the shared drive.
open(MAIL, "|/usr/sbin/sendmail -t 2>/dev/null") and do {
    print MAIL "To: $MAILTO\n";
    print MAIL "Subject: [AUTO] Finance billing report $stamp\n";
    print MAIL "\nAttached... well, saved to $xls on the ETL box.\n";
    close(MAIL);
};

print scalar(localtime), " finance_excel_report done\n";
exit 0;
