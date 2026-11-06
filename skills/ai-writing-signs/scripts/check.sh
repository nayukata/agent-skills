#!/usr/bin/env bash
# macOS の grep は絵文字のバイト範囲指定で illegal byte sequence になるため、検索は perl で行う。
set -eu
[ $# -ge 1 ] || { echo "usage: $0 <file>" >&2; exit 2; }
f=$1
[ -f "$f" ] || { echo "not found: $f" >&2; exit 2; }

# 行番号を元ファイルと一致させるため、コードブロックは削除せず空行に置き換える
body=$(awk 'NR==1 && /^---$/{fm=1; print ""; next} fm{print ""; if(/^---$/) fm=0; next} /^```/{skip=!skip; print ""; next} skip{print ""; next} {print}' "$f" | sed -E 's/`[^`]*`//g')

run() { # run <種別> <正規表現> [表示する最小件数]
  local label=$1 re=$2 min=${3:-1} hits n
  hits=$(printf '%s\n' "$body" | perl -CSDA -ne 'BEGIN{$re=shift} print "$.:$_" if /$re/' "$re")
  n=$( [ -n "$hits" ] && printf '%s\n' "$hits" | wc -l | tr -d ' ' || echo 0)
  if [ "$n" -lt "$min" ]; then
    [ "$min" -gt 1 ] && printf '## %s: %s 件 (%s 件未満は表示しない)\n\n' "$label" "$n" "$min" || printf '## %s: 0 件\n\n' "$label"
    return
  fi
  printf '## %s: %s 件\n' "$label" "$n"
  [ -n "$hits" ] && printf '%s\n' "$hits" | cut -c1-140
  echo
}

run "予告の型"       '結論から(言|い)うと|結論から言えば|この記事では|本記事では|本稿では'
run "否定の対句"     'ではなく|だけでなく|というより(も)?|のみならず' 3
run "空虚な強調"     '非常に|極めて|本質的に|根本的に|不可欠|大いに'
run "「〜の形」でぼかす" '形にな(る|り|っ)|の形で|形にする|形に寄|という形'
run "「〜の側」の構文" 'の側(で|の|に|は|が|を|から|へ|と|も|、)'
run "鉤括弧の強調 (引用でない可能性)" '「[^」]{1,12}」(と判定|という|として|と呼|的な|を意識|を重視)'
run "「〇〇」の〇〇は"  '「[^」]+」の[^、。「」]{1,10}は'
run "疑問符の無い問いかけの台詞" '^[ \x{3000}]*「(?![^」]*[？?])[^」]*(の|よね|だっけ|っけ|何|なに|なんで|どこが|誰が|は|どう|どうって|どこ|誰|いつ|どういうとこ|どういうところ)」$'
run "逆接の「のに」の後に依頼・勧誘" 'のに、[^。」]*(ください|てほしい|しよう|しなさい)'
run "動詞の句を鉤括弧で名詞扱い (引用・画面の文言なら対象外)" '「[^」「]{2,}[うくぐすつぬぶむるたい]」(は|が|も|こそ)'
run "太字の乱用 (表以外の1行に2か所以上)" '^[^|]*(\*\*[^*]+\*\*.*){2,}'

printf '## 見出し付き箇条書きの連続 (3 行以上)\n'
printf '%s\n' "$body" | awk '
  /^[ \t]*[-*] \*\*[^*]+\*\*[:：]/ { if (!run) start=NR; run++; next }
  { if (run>=3) print "行 " start "-" NR-1 ": " run " 行"; run=0 }
  END { if (run>=3) print "行 " start "-" NR ": " run " 行" }'
echo

printf '## 文末の単調 (段落内で同語尾 4 回以上)\n'
printf '%s\n' "$body" | awk '
  function flush() { if (d>=4||m>=4||k>=2) print "行 " start " からの段落: です=" d " ます=" m " になります=" k; d=m=k=0; start=0 }
  /^[ \t]*$/ { flush(); next }
  { if (!start) start=NR; d+=gsub(/です。/,"&"); m+=gsub(/ます。/,"&"); k+=gsub(/になります。/,"&") }
  END { flush() }'
echo

printf '## 見出しだけの節 (同じか浅い階層の見出しが本文なしで続く)\n'
printf '%s\n' "$body" | awk '
  /^#+ / { match($0,/^#+/); d=RLENGTH; if (prev!="" && d<=pd) print "行 " pl ": " prev; prev=$0; pl=NR; pd=d; next }
  /^[ \t]*$/ { next }
  { prev="" }'
echo

# macOS の awk はバイト単位で、日本語の「先頭4文字」を切り出せないため perl で書く
printf '## 段落の書き出しの反復 (同じ書き出しが 2 段落以上)\n'
printf '%s\n' "$body" | perl -Mutf8 -CSD -0777 -ne '
  my @lines = split /\n/, $_, -1;
  my $ln = 0;
  my (@cur, $start, @paras);
  for my $line (@lines) {
    $ln++;
    if ($line =~ /^\s*$/) {
      push @paras, { start => $start, lines => [@cur] } if @cur;
      @cur = (); $start = undef;
      next;
    }
    $start = $ln unless defined $start;
    push @cur, $line;
  }
  push @paras, { start => $start, lines => [@cur] } if @cur;

  my (%count, %lines_for);
  for my $p (@paras) {
    my $first = $p->{lines}[0];
    next if $first =~ /^#/;
    next if $first =~ /^[-*]\s/;
    next if $first =~ /^>/;
    next if $first =~ /^\|/;
    my $text = join("", @{$p->{lines}});
    $text =~ s/^\*\*//;
    my $head = substr($text, 0, 4);
    next if length($head) < 4;
    $count{$head}++;
    push @{$lines_for{$head}}, $p->{start};
  }
  for my $k (sort { $count{$b} <=> $count{$a} } keys %count) {
    next unless $count{$k} >= 2;
    print "書き出し「$k」: $count{$k} 段落 (行 " . join(", ", @{$lines_for{$k}}) . ")\n";
  }
'
echo

printf '## 短い文の不足 (8 文以上あるのに 20 字以下の文が 1 つ以下)\n'
printf '%s\n' "$body" | perl -Mutf8 -CSD -0777 -ne '
  my @lines = split /\n/, $_, -1;
  my (@cur, @paras);
  for my $line (@lines) {
    if ($line =~ /^\s*$/) {
      push @paras, [@cur] if @cur;
      @cur = ();
      next;
    }
    push @cur, $line;
  }
  push @paras, [@cur] if @cur;

  my ($total, $short) = (0, 0);
  for my $p (@paras) {
    my $first = $p->[0];
    next if $first =~ /^#/;
    next if $first =~ /^[-*]\s/;
    next if $first =~ /^>/;
    next if $first =~ /^\|/;
    my $text = join("", @$p);
    for my $s ($text =~ /(.*?。)/g) {
      $total++;
      $short++ if length($s) <= 20;
    }
  }
  print "文 $total / 20字以下 $short\n" if $total >= 8 && $short <= 1;
'
