# Cron Box Atlas namespace bootstrap

`cronbox_namespace.py` owns only the `ow_tp_demo` database and its
`documents` and `files` collections on the existing Atlas M0 cluster. Atlas
Search index definitions belong to the cron-search child and are intentionally
absent here. The script never changes cluster settings.

Safe validation:

```sh
python3 scripts/tp_atlas/cronbox_namespace.py --dry-run
```

The parent owns the write:

```sh
MONGODB_ATLAS_URI=... python3 scripts/tp_atlas/cronbox_namespace.py --apply
```

The URI is read from the environment and never printed.
