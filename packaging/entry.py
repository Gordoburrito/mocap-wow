"""Entry point for the bundled Mac app (see packaging/build_app.sh)."""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from mocap.app import main
    main()
