create table qlserver(
  id 		integer primary key,
  host		text,
  status	text
);

create table quicklists(
  uuid		text,
  owner		text,
  name		text
);

create table tickers(
  uuid		text,
  asset		text,
  quicklist	text,
  exchange	text,
  symbol	text
);

create table owners(
  uuid		text,
  seq		integer
);
