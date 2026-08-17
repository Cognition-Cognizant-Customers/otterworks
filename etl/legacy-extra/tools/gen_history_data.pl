#!/usr/bin/perl
#############################################################
# gen_history_data.pl — deterministic CUSTBILL history generator
#
# Produces one fixed-width CUSTBILL extract per calendar month
# across a range of years, standing in for the years of monthly
# mainframe drops that accumulated in $ROOT/archive on the old
# ETL box. Files land under sftp-drop/history/<YYYY>/ and keep a
# source file date (mtime) of the last day of their month, so a
# backfill can partition on the drop date rather than load time.
#
# Deterministic: the seed is derived from NS and the year+month,
# so the same arguments reproduce byte-identical files.
#
# Usage: perl gen_history_data.pl [NS] [START_YEAR] [END_YEAR] [ROWS_PER_MONTH]
#   NS defaults to "dev", START_YEAR to 2019, END_YEAR to 2024,
#   ROWS_PER_MONTH to 40.
#
# Alongside the data it writes an expectation manifest at
#   sftp-drop/history/expected/<NS>-history-expected.json
# holding per-year counts, per-currency/record-type totals in
# cents, and the exact set of planted anomalies. That manifest is
# the source of truth a backfill reconciliation compares against,
# independent of any platform.
#
# (Demo harness tool, not part of the legacy estate itself — it
# stands in for the mainframe job CB77340 running every month.)
#############################################################
use strict;
use warnings;

my $ns    = $ARGV[0] || $ENV{"NS"} || "dev";
my $y0    = $ARGV[1] || $ENV{"START_YEAR"} || 2019;
my $y1    = $ARGV[2] || $ENV{"END_YEAR"} || 2024;
my $nrows = $ARGV[3] || $ENV{"ROWS_PER_MONTH"} || 40;

die "START_YEAR must be a 4-digit year: $y0\n" unless $y0 =~ /^\d{4}$/;
die "END_YEAR must be a 4-digit year: $y1\n"   unless $y1 =~ /^\d{4}$/;
die "END_YEAR must be >= START_YEAR\n"         unless $y1 >= $y0;
die "ROWS_PER_MONTH must be a positive integer: $nrows\n" unless $nrows =~ /^\d+$/ && $nrows > 0;
die "NS must match [A-Za-z0-9_-]+: $ns\n"       unless $ns =~ /^[A-Za-z0-9_-]+$/;

my $root = $ENV{"OTTERWORKS_LEGACY_ROOT"} || "/tmp/otterworks-legacy";
my $hist = "$root/sftp-drop/history";
my $expd = "$hist/expected";
system("mkdir", "-p", $expd) == 0 or die "cannot create $expd\n";

my @first = qw(ACME GLOBEX INITECH UMBRELLA STARK WAYNE TYRELL WONKA HOOLI PIED);
my @last  = qw(HOLDINGS INDUSTRIES LLC CORP PARTNERS GMBH SA LTD GROUP PIPER);
my @ccy   = qw(USD EUR GBP);
my @dim   = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31);

# same LCG shape as gen_sample_data.pl so the two generators stay comparable
my $state = 0;
sub lcg { $state = ($state * 1103515245 + 12345) % 2**31; return $state; }
sub rnd { my ($n) = @_; return int(lcg() / 2147483648 * $n); }

sub seed_for {
    my ($namespace, $yyyymm) = @_;
    my $seed = 0;
    my $pos  = 1;
    for my $c (split //, uc($namespace) . $yyyymm) { $seed += ord($c) * $pos; $pos++; }
    return $seed * 2654435761 % 2**31;
}

sub days_in_month {
    my ($y, $m) = @_;
    return 29 if $m == 2 && (($y % 4 == 0 && $y % 100 != 0) || $y % 400 == 0);
    return $dim[$m - 1];
}

sub jstr { my ($s) = @_; $s =~ s/(["\\])/\\$1/g; return "\"$s\""; }

my (@files, @anomalies, %year_cnt, %year_amt, %year_bad);

for my $y ($y0 .. $y1) {
    my $dir = "$hist/$y";
    system("mkdir", "-p", $dir) == 0 or die "cannot create $dir\n";
    for my $m (1 .. 12) {
        my $yyyymm = sprintf("%04d%02d", $y, $m);
        $state = seed_for($ns, $yyyymm);
        my $fname = sprintf("CUSTBILL_%s_%s.dat", uc($ns), $yyyymm);
        my $path  = "$dir/$fname";
        my $dim_m = days_in_month($y, $m);

        # deterministic planted defects, spread across the history so every
        # year contains at least one of each class
        my $bad_date_row  = ($m % 6 == 3) ? 7  : 0;
        my $bad_amt_row   = ($m % 6 == 5) ? 11 : 0;
        my $trailer_drift = ($m == 12)    ? 1  : 0;
        $bad_date_row = 0 if $bad_date_row > $nrows;
        $bad_amt_row  = 0 if $bad_amt_row  > $nrows;

        open my $out, ">", $path or die "cannot write $path: $!";
        printf $out "HDR CUSTBILL EXTRACT NS=%-10s PERIOD=%s%s\n", uc($ns), $yyyymm, " " x 16;
        my $total = 0;
        for my $r (1 .. $nrows) {
            my $cust = sprintf("C%09d", 100000 + rnd(900000));
            my $name = sprintf("%-30.30s", $first[rnd(scalar @first)] . " " . $last[rnd(scalar @last)]);
            my $dd   = 1 + rnd($dim_m);
            my $date = sprintf("%04d%02d%02d", $y, $m, $dd);
            my $cents = 100 + rnd(999900);
            my $amt  = sprintf("%012d", $cents);
            my $cur  = $ccy[rnd(scalar @ccy)];
            my $rt   = (rnd(10) < 8) ? "01" : "02";
            my $kind = "";
            if ($r == $bad_date_row) {
                $date = sprintf("%04d0231", $y);   # Feb 31st: no such day
                $kind = "invalid_calendar_date";
            } elsif ($r == $bad_amt_row) {
                $amt  = "00000ABC1234";            # non-numeric in a PIC 9 field
                $kind = "nonnumeric_amount";
            }
            if ($kind) {
                push @anomalies, { file => $fname, row => $r, cust_id => $cust, kind => $kind };
                $year_bad{$y}++;
            } else {
                $year_cnt{$y}{"$cur|$rt"}++;
                $year_amt{$y}{"$cur|$rt"} += $cents;
            }
            printf $out "%s%s%s%s%s%s\n", $cust, $name, $date, $amt, $cur, $rt;
            $total++;
        }
        printf $out "TRL%010d%s\n", $total + $trailer_drift, " " x 52;
        close $out;

        # source file date = last second of the month it covers
        my $epoch = month_end_epoch($y, $m, $dim_m);
        utime $epoch, $epoch, $path or die "cannot set mtime on $path: $!";

        push @files, {
            name => $fname, year => $y, month => $m, period => $yyyymm,
            records => $total, trailer_count => $total + $trailer_drift,
            trailer_matches => ($trailer_drift ? 0 : 1), mtime_epoch => $epoch,
        };
        if ($trailer_drift) {
            push @anomalies, { file => $fname, row => 0, cust_id => "", kind => "trailer_count_mismatch" };
        }
        print "wrote $path ($total records, period $yyyymm)\n";
    }
}

sub month_end_epoch {
    my ($y, $m, $dim_m) = @_;
    # days since epoch for YYYY-MM-<last>, computed without Time::Local so the
    # tool keeps the estate's "no CPAN, no modules" constraint
    my $days = 0;
    for my $yy (1970 .. $y - 1) { $days += ((($yy % 4 == 0 && $yy % 100 != 0) || $yy % 400 == 0) ? 366 : 365); }
    for my $mm (1 .. $m - 1)    { $days += days_in_month($y, $mm); }
    $days += $dim_m - 1;
    return $days * 86400 + 23 * 3600 + 59 * 60 + 59;
}

my $manifest = "$expd/" . lc($ns) . "-history-expected.json";
open my $mf, ">", $manifest or die "cannot write $manifest: $!";
print $mf "{\n";
print $mf "  \"kind\": \"custbill-history-expectations\",\n";
print $mf "  \"generator\": \"gen_history_data.pl\",\n";
print $mf "  \"namespace\": " . jstr(lc($ns)) . ",\n";
print $mf "  \"start_year\": $y0,\n  \"end_year\": $y1,\n  \"rows_per_month\": $nrows,\n";
print $mf "  \"file_count\": " . scalar(@files) . ",\n";
my $all_rows = 0;
$all_rows += $_->{records} for @files;
print $mf "  \"record_count\": $all_rows,\n";
print $mf "  \"anomaly_count\": " . scalar(@anomalies) . ",\n";
print $mf "  \"per_year\": [\n";
my @yblocks;
for my $y ($y0 .. $y1) {
    my $rows = 0;
    $rows += $_->{records} for grep { $_->{year} == $y } @files;
    my @tot;
    for my $key (sort keys %{ $year_cnt{$y} || {} }) {
        my ($cur, $rt) = split /\|/, $key;
        push @tot, sprintf(
            "        {\"currency\": \"%s\", \"record_type\": \"%s\", \"record_count\": %d, \"total_amount_cents\": %d}",
            $cur, $rt, $year_cnt{$y}{$key}, $year_amt{$y}{$key});
    }
    push @yblocks, "    {\n      \"year\": $y,\n"
        . "      \"record_count\": $rows,\n"
        . "      \"quarantine_record_count\": " . ($year_bad{$y} || 0) . ",\n"
        . "      \"totals\": [\n" . join(",\n", @tot) . "\n      ]\n    }";
}
print $mf join(",\n", @yblocks) . "\n  ],\n";
print $mf "  \"planted_anomalies\": [\n";
print $mf join(",\n", map {
    sprintf("    {\"file\": %s, \"row\": %d, \"cust_id\": %s, \"kind\": %s}",
        jstr($_->{file}), $_->{row}, jstr($_->{cust_id}), jstr($_->{kind}))
} @anomalies) . "\n  ],\n";
print $mf "  \"files\": [\n";
print $mf join(",\n", map {
    sprintf("    {\"file\": %s, \"year\": %d, \"month\": %d, \"period\": %s, \"records\": %d, \"trailer_count\": %d, \"trailer_matches\": %s, \"mtime_epoch\": %d}",
        jstr($_->{name}), $_->{year}, $_->{month}, jstr($_->{period}),
        $_->{records}, $_->{trailer_count}, ($_->{trailer_matches} ? "true" : "false"), $_->{mtime_epoch})
} @files) . "\n  ]\n";
print $mf "}\n";
close $mf;
print "wrote $manifest (" . scalar(@files) . " files, $all_rows records, " . scalar(@anomalies) . " planted anomalies)\n";
