#!/usr/bin/perl
#############################################################
# gen_sample_data.pl — deterministic CUSTBILL feed generator
#
# Produces small fixed-width mainframe-style CUSTBILL files in
# the SFTP drop directory so the legacy jobs have real input.
# Deterministic: the seed is derived from the NS parameter, so
# the same namespace reproduces byte-identical files.
#
# Usage: perl gen_sample_data.pl [NS] [NFILES] [ROWS_PER_FILE]
#   NS defaults to "dev", NFILES to 2, ROWS_PER_FILE to 50.
#
# (This is a demo harness tool, not part of the legacy estate
# itself — it stands in for the mainframe job CB77340.)
#############################################################
use strict;
use warnings;

my $ns    = $ARGV[0] || $ENV{"NS"} || "dev";
my $nfile = $ARGV[1] || 2;
my $nrows = $ARGV[2] || 50;

my $root = $ENV{"OTTERWORKS_LEGACY_ROOT"} || "/tmp/otterworks-legacy";
my $drop = "$root/sftp-drop/upload";
system("mkdir", "-p", $drop);

# seed derived from NS: sum of char codes (stable, portable)
my $seed = 0;
$seed += ord($_) for split //, $ns;
$seed = $seed * 2654435761 % 2**31;

# tiny deterministic LCG so output doesn't depend on perl's rand()
my $state = $seed;
sub lcg { $state = ($state * 1103515245 + 12345) % 2**31; return $state; }
sub rnd { my ($n) = @_; return lcg() % $n; }

my @first = qw(ACME GLOBEX INITECH UMBRELLA STARK WAYNE TYRELL WONKA HOOLI PIED);
my @last  = qw(HOLDINGS INDUSTRIES LLC CORP PARTNERS GMBH SA LTD GROUP PIPER);
my @ccy   = qw(USD EUR GBP);

for my $fi (1 .. $nfile) {
    my $fname = sprintf("CUSTBILL_%s_%03d.dat", uc($ns), $fi);
    open my $out, ">", "$drop/$fname" or die "cannot write $drop/$fname: $!";
    printf $out "HDR CUSTBILL EXTRACT NS=%-10s FILE=%03d%s\n", uc($ns), $fi, " " x 20;
    my $total = 0;
    for my $r (1 .. $nrows) {
        my $cust = sprintf("C%09d", 100000 + rnd(900000));
        my $name = sprintf("%-30.30s", $first[rnd(scalar @first)] . " " . $last[rnd(scalar @last)]);
        my $yyyy = 2024 + rnd(2);
        my $mm   = 1 + rnd(12);
        my $dd   = 1 + rnd(28);
        my $date = sprintf("%04d%02d%02d", $yyyy, $mm, $dd);
        my $amt  = 100 + rnd(999900);          # cents, implied decimal
        my $rt   = (rnd(10) < 8) ? "01" : "02"; # mostly invoices
        printf $out "%s%s%s%012d%s%s\n",
            $cust, $name, $date, $amt, $ccy[rnd(scalar @ccy)], $rt;
        $total++;
    }
    printf $out "TRL%010d%s\n", $total, " " x 52;
    close $out;
    print "wrote $drop/$fname ($total records)\n";
}
