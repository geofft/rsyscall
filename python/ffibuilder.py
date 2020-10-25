from cffi import FFI
import os
import pathlib
import shutil

ffibuilder = FFI()
ffibuilder.set_source_pkgconfig(
    "rsyscall._raw", ["rsyscall"], """
#include <asm/types.h>
#include <dirent.h>
#include <fcntl.h>
#include <linux/capability.h>
#include <linux/if_tun.h>
#include <linux/netlink.h>
#include <linux/futex.h>
#include <linux/fuse.h>
#include <net/if.h>
#include <netinet/ip.h>
#include <netinet/ip.h>
#include <poll.h>
#include <rsyscall.h>
#include <sched.h>
#include <setjmp.h>
#include <string.h>
#include <sys/epoll.h>
#include <sys/eventfd.h>
#include <sys/timerfd.h>
#include <sys/inotify.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/ptrace.h>
#include <sys/resource.h>
#include <sys/signal.h>
#include <sys/signalfd.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <sys/uio.h>
#include <syscall.h>
#include <unistd.h>
#include <limits.h>

struct linux_dirent64 {
    ino64_t        d_ino;    /* 64-bit inode number */
    off64_t        d_off;    /* 64-bit offset to next structure */
    unsigned short d_reclen; /* Size of this dirent */
    unsigned char  d_type;   /* File type */
    char           d_name[]; /* Filename (null-terminated) */
};

// the double underscores are hard to use from Python,
// since they will be replaced with the class name
#define _WNOTHREAD __WNOTHREAD
#define _WCLONE __WCLONE
#define _WALL __WALL
#define _ss_padding __ss_padding

// glibc hides the real O_LARGEFILE from us - it lies to us! evil!
#undef O_LARGEFILE
#define O_LARGEFILE 0100000

#define SA_RESTORER 0x04000000
typedef void (*sighandler_t)(int);
typedef void (*sigrestore_t)(void);
// we assume NSIGNALS == 64, so we don't need any more than this
struct kernel_sigset {
    unsigned long int val;
};
struct kernel_sigaction {
	sighandler_t ksa_handler;
	unsigned long ksa_flags;
	sigrestore_t ksa_restorer;
	struct kernel_sigset ksa_mask;
};
struct fdpair {
    int first;
    int second;
};
struct futex_node {
  struct robust_list list;
  uint32_t futex;
};

// there are some buggy headers on older systems which have a negative value for EPOLLET :(
#undef EPOLLET
#define EPOLLET 0x80000000
""")
ffibuilder.cdef("""
typedef union epoll_data {
    uint64_t u64;
} epoll_data_t;
""")
ffibuilder.cdef("""
struct epoll_event {
  uint32_t     events;
  epoll_data_t data;
};
""", packed=True)
ffibuilder.cdef("""
struct fdpair {
    int first;
    int second;
};

typedef signed... time_t;

struct timespec {
    time_t tv_sec;                /* Seconds */
    long   tv_nsec;               /* Nanoseconds */
};

int epoll_wait(int epfd, struct epoll_event *events, int maxevents, int timeout);
int epoll_create1(int flags);
int epoll_ctl(int epfd, int op, int fd, struct epoll_event *event);

#define EPOLL_CTL_ADD ...
#define EPOLL_CTL_MOD ...
#define EPOLL_CTL_DEL ...

#define AT_FDCWD ...
#define AT_EMPTY_PATH ...
#define AT_SYMLINK_NOFOLLOW ...
#define AT_SYMLINK_FOLLOW ...
#define AT_REMOVEDIR ...

int unlinkat(int dirfd, const char *pathname, int flags);
int linkat(int olddirfd, const char *oldpath, int newdirfd, const char *newpath, int flags);
long rsyscall_raw_syscall(long arg1, long arg2, long arg3, long arg4, long arg5, long arg6, long sys);

#define EPOLL_CLOEXEC ...

typedef unsigned... ino64_t;
typedef signed... off64_t;

#define DT_BLK ... // This is a block device.
#define DT_CHR ... // This is a character device.
#define DT_DIR ... // This is a directory.
#define DT_FIFO ... // This is a named pipe (FIFO).
#define DT_LNK ... // This is a symbolic link.
#define DT_REG ... // This is a regular file.
#define DT_SOCK ... // This is a UNIX domain socket.
#define DT_UNKNOWN ... // The file type is unknown.

struct linux_dirent64 {
    ino64_t        d_ino;    /* 64-bit inode number */
    off64_t        d_off;    /* 64-bit offset to next structure */
    unsigned short d_reclen; /* Size of this dirent */
    unsigned char  d_type;   /* File type */
    char           d_name[]; /* Filename (null-terminated) */
};

// needed to determine true length of the null-terminated filenames, which are null-padded
size_t strlen(const char *s);

int faccessat(int dirfd, const char *pathname, int mode, int flags);

// signal stuff
#define SYS_waitid ...
#define SYS_kill ...

#define WEXITED ...
#define WSTOPPED ...
#define WCONTINUED ...
#define WNOHANG ...
#define WNOWAIT ...
#define _WCLONE ...
#define _WALL ...
#define _WNOTHREAD ...
#define P_PID ...
#define P_PGID ...
#define P_ALL ...
#define CLD_EXITED ... // child called _exit(2)
#define CLD_KILLED ... // child killed by signal
#define CLD_DUMPED ... // child killed by signal, and dumped core
#define CLD_STOPPED ... // child stopped by signal
#define CLD_TRAPPED ... // traced child has trapped
#define CLD_CONTINUED ... // child continued by SIGCONT
typedef int... pid_t;
typedef unsigned... uid_t;
typedef struct siginfo {
    int      si_signo;
    int      si_code;      /* Signal code */
    pid_t    si_pid;       /* Sending process ID */
    uid_t    si_uid;       /* Real user ID of sending process */
    int      si_status;    /* Exit value or signal */
    ...;
} siginfo_t;

#define SA_NOCLDSTOP ...
#define SA_NOCLDWAIT ...
#define SA_NODEFER ...
#define SA_ONSTACK ...
#define SA_RESETHAND ...
#define SA_RESTART ...
#define SA_SIGINFO ...
#define SA_RESTORER ...

typedef void (*sighandler_t)(int);
typedef void (*sigrestore_t)(void);
struct kernel_sigset {
    unsigned long int val;
};
struct kernel_sigaction {
	sighandler_t ksa_handler;
	unsigned long ksa_flags;
	sigrestore_t ksa_restorer;
	struct kernel_sigset ksa_mask;
};
#define SYS_rt_sigaction ...

#define SYS_signalfd4 ...

#define SFD_NONBLOCK ...
#define SFD_CLOEXEC ...

#define SYS_rt_sigprocmask ...

#define SIG_BLOCK ...
#define SIG_UNBLOCK ...
#define SIG_SETMASK ...

#define SIGSTKFLT ...

struct signalfd_siginfo {
    uint32_t ssi_signo;    /* Signal number */
    int32_t  ssi_errno;    /* Error number (unused) */
    int32_t  ssi_code;     /* Signal code */
    uint32_t ssi_pid;      /* PID of sender */
    uint32_t ssi_uid;      /* Real UID of sender */
    int32_t  ssi_fd;       /* File descriptor (SIGIO) */
    uint32_t ssi_tid;      /* Kernel timer ID (POSIX timers)
    uint32_t ssi_band;     /* Band event (SIGIO) */
    uint32_t ssi_overrun;  /* POSIX timer overrun count */
    uint32_t ssi_trapno;   /* Trap number that caused signal */
    int32_t  ssi_status;   /* Exit status or signal (SIGCHLD) */
    int32_t  ssi_int;      /* Integer sent by sigqueue(3) */
    uint64_t ssi_ptr;      /* Pointer sent by sigqueue(3) */
    uint64_t ssi_utime;    /* User CPU time consumed (SIGCHLD) */
    uint64_t ssi_stime;    /* System CPU time consumed
                              (SIGCHLD) */
    ...;
};

#define SYS_splice ...

// preadv2
#define SYS_preadv2 ...
#define SYS_pwritev2 ...

#define RWF_DSYNC ...
#define RWF_HIPRI ...
#define RWF_SYNC ...

// fs syscalls
#define SYS_openat ...
#define SYS_read ...
#define SYS_write ...
#define SYS_pread64 ...
#define SYS_pwrite64 ...
#define SYS_close ...
#define SYS_dup3 ...
#define SYS_pipe2 ...
#define SYS_ftruncate ...
#define SYS_ioctl ...
#define SYS_chmod ...
#define SYS_fchmod ...
#define SYS_fchmodat ...

#define SYS_chdir ...
#define SYS_fchdir ...
#define SYS_chroot ...

#define SYS_mkdirat ...
#define SYS_getdents64 ...
#define SYS_unlinkat ...
#define SYS_linkat ...
#define SYS_renameat2 ...

#define RENAME_EXCHANGE ...
#define RENAME_NOREPLACE ...
#define RENAME_WHITEOUT ...

#define SYS_symlinkat ...
#define SYS_readlinkat ...

#define O_RDONLY ...
#define O_WRONLY ...
#define O_RDWR ...
#define O_CREAT ...
#define O_EXCL ...
#define O_NOCTTY ...
#define O_TRUNC ...
#define O_APPEND ...
#define O_NONBLOCK ...
#define O_DSYNC ...
#define O_DIRECT ...
#define O_LARGEFILE ...
#define O_DIRECTORY ...
#define O_NOFOLLOW ...
#define O_NOATIME ...
#define O_CLOEXEC ...
#define O_SYNC ...
#define O_PATH ...
#define O_TMPFILE ...

#define SYS_lseek ...

#define SEEK_SET ...
#define SEEK_CUR ...
#define SEEK_END ...
#define SEEK_DATA ...
#define SEEK_HOLE ...

#define SYS_faccessat ...

#define R_OK ...
#define W_OK ...
#define X_OK ...
#define F_OK ...

// stat
#define SYS_fstat ...

#define S_IFMT   ...
#define S_IFSOCK ...
#define S_IFLNK	 ...
#define S_IFREG  ...
#define S_IFBLK  ...
#define S_IFDIR  ...
#define S_IFCHR  ...
#define S_IFIFO  ...
#define S_ISUID  ...
#define S_ISGID  ...
#define S_ISVTX  ...

#define S_IRWXU ...
#define S_IRUSR ...
#define S_IWUSR ...
#define S_IXUSR ...

#define S_IRWXG ...
#define S_IRGRP ...
#define S_IWGRP ...
#define S_IXGRP ...

#define S_IRWXO ...
#define S_IROTH ...
#define S_IWOTH ...
#define S_IXOTH ...

struct stat {
	unsigned long	st_dev;
	unsigned long	st_ino;
	unsigned long	st_nlink;

	unsigned int		st_mode;
	unsigned int		st_uid;
	unsigned int		st_gid;
	unsigned int		__pad0;
	unsigned long	st_rdev;
	long		st_size;
	long		st_blksize;
	long		st_blocks;	/* Number 512-byte blocks allocated. */

	struct timespec	st_atim;
	struct timespec	st_mtim;
	struct timespec	st_ctim;
	...;
};

// prctl
#define SYS_prctl ...
#define PR_SET_PDEATHSIG ...

#define SYS_getpid ...
#define SYS_setsid ...

#define SYS_setpgid ...
#define SYS_getpgid ...

// capabilities

#define SYS_capset ...
#define SYS_capget ...

#define _LINUX_CAPABILITY_VERSION_3 ...

#define PR_CAP_AMBIENT ...
#define PR_CAP_AMBIENT_RAISE ...

#define CAP_CHOWN ...
#define CAP_DAC_OVERRIDE ...
#define CAP_DAC_READ_SEARCH ...
#define CAP_FOWNER ...
#define CAP_FSETID ...
#define CAP_KILL ...
#define CAP_SETGID ...
#define CAP_SETUID ...
#define CAP_SETPCAP ...
#define CAP_LINUX_IMMUTABLE ...
#define CAP_NET_BIND_SERVICE ...
#define CAP_NET_BROADCAST ...
#define CAP_NET_ADMIN ...
#define CAP_NET_RAW ...
#define CAP_IPC_LOCK ...
#define CAP_IPC_OWNER ...
#define CAP_SYS_MODULE ...
#define CAP_SYS_RAWIO ...
#define CAP_SYS_CHROOT ...
#define CAP_SYS_PTRACE ...
#define CAP_SYS_PACCT ...
#define CAP_SYS_ADMIN ...
#define CAP_SYS_BOOT ...
#define CAP_SYS_NICE ...
#define CAP_SYS_RESOURCE ...
#define CAP_SYS_TIME ...
#define CAP_SYS_TTY_CONFIG ...
#define CAP_MKNOD ...
#define CAP_LEASE ...
#define CAP_AUDIT_WRITE ...
#define CAP_AUDIT_CONTROL ...
#define CAP_SETFCAP ...
#define CAP_MAC_OVERRIDE ...
#define CAP_MAC_ADMIN ...
#define CAP_SYSLOG ...
#define CAP_WAKE_ALARM ...
#define CAP_BLOCK_SUSPEND ...
#define CAP_AUDIT_READ ...

struct __user_cap_data_struct {
    uint32_t effective;
    uint32_t permitted;
    uint32_t inheritable;
};
struct __user_cap_header_struct {
    uint32_t version;
    int pid;
};

// epoll stuff
#define SYS_epoll_ctl ...
#define SYS_epoll_wait ...
#define SYS_epoll_create1 ...

#define EPOLLIN ...
#define EPOLLOUT ...
#define EPOLLRDHUP ...
#define EPOLLPRI ...
#define EPOLLERR ...
#define EPOLLHUP ...
#define EPOLLET ...

// poll stuff
#define SYS_poll ...

struct pollfd {
    int   fd;         /* file descriptor */
    short events;     /* requested events */
    short revents;    /* returned events */
};

#define POLLIN ...
#define POLLHUP ...
#define POLLERR ...
#define POLLNVAL ...

//// eventfd
#define SYS_eventfd2 ...

#define EFD_CLOEXEC ...
#define EFD_NONBLOCK ...
#define EFD_SEMAPHORE ...

//// timerfd
#define SYS_timerfd_create ...
#define SYS_timerfd_settime ...
#define SYS_timerfd_gettime ...

#define CLOCK_REALTIME ...
#define CLOCK_MONOTONIC ...
#define CLOCK_BOOTTIME ...
#define CLOCK_REALTIME_ALARM ...
#define CLOCK_BOOTTIME_ALARM ...

#define TFD_NONBLOCK ...
#define TFD_CLOEXEC ...

#define TFD_TIMER_ABSTIME ...
#define TFD_TIMER_CANCEL_ON_SET ...

struct itimerspec {
    struct timespec it_interval;  /* Interval for periodic timer */
    struct timespec it_value;     /* Initial expiration */
};

//// inotify
#define SYS_inotify_init1 ...
#define IN_NONBLOCK ...
#define IN_CLOEXEC ...

#define SYS_inotify_add_watch ...
#define SYS_inotify_rm_watch ...

struct inotify_event {
    int      wd;       /* Watch descriptor */
    uint32_t mask;     /* Mask describing event */
    uint32_t cookie;   /* Unique cookie associating related
                          events (for rename(2)) */
    uint32_t len;      /* Size of name field */
    char     name[];   /* Optional null-terminated name */
};

#define NAME_MAX ...

// events settable in both
#define IN_ACCESS ...
#define IN_ATTRIB ...
#define IN_CLOSE_WRITE ...
#define IN_CLOSE_NOWRITE ...
#define IN_CREATE ...
#define IN_DELETE ...
#define IN_DELETE_SELF ...
#define IN_MODIFY ...
#define IN_MOVE_SELF ...
#define IN_MOVED_FROM ...
#define IN_MOVED_TO ...
#define IN_OPEN ...
// additional options to inotify_add_watch
#define IN_DONT_FOLLOW ...
#define IN_EXCL_UNLINK ...
#define IN_MASK_ADD ...
#define IN_ONESHOT ...
#define IN_ONLYDIR ...
// additional bits returned in struct inotify_event 
#define IN_IGNORED ...
#define IN_ISDIR ...
#define IN_Q_OVERFLOW ...
#define IN_UNMOUNT ...

//// task stuff
#define SYS_clone ...
#define SYS_vfork ...
#define SYS_exit ...
#define SYS_exit_group ...
#define SYS_execve ...
#define SYS_execveat ...
#define SYS_unshare ...
#define SYS_setns ...
#define SYS_set_tid_address ...

#define SYS_getuid ...
#define SYS_getgid ...

#define CLONE_VFORK ...
#define CLONE_CHILD_CLEARTID ...
#define CLONE_PARENT ...

#define CLONE_VM ...
#define CLONE_SIGHAND ...
#define CLONE_IO ...
#define CLONE_SYSVSEM ...

#define CLONE_FILES ...
#define CLONE_FS ...
#define CLONE_NEWCGROUP ...
#define CLONE_NEWIPC ...
#define CLONE_NEWNET ...
#define CLONE_NEWNS ...
#define CLONE_NEWPID ...
#define CLONE_NEWUSER ...
#define CLONE_NEWUTS ...

// sched_{set,get}affinity stuff

#define SYS_sched_setaffinity ...
#define SYS_sched_getaffinity ...

typedef struct {
  unsigned long __bits[...];
} cpu_set_t;

// getpriority/setpriority

#define PRIO_PROCESS ...
#define PRIO_PGRP ...
#define PRIO_USER ...

#define SYS_getpriority ...
#define SYS_setpriority ...

// prlimit

#define RLIMIT_AS ...
#define RLIMIT_CORE ...
#define RLIMIT_CPU ...
#define RLIMIT_DATA ...
#define RLIMIT_FSIZE ...
#define RLIMIT_LOCKS ...
#define RLIMIT_MEMLOCK ...
#define RLIMIT_MSGQUEUE ...
#define RLIMIT_NICE ...
#define RLIMIT_NOFILE ...
#define RLIMIT_NPROC ...
#define RLIMIT_RSS ...
#define RLIMIT_RTPRIO ...
#define RLIMIT_RTTIME ...
#define RLIMIT_SIGPENDING ...
#define RLIMIT_STACK ...

struct rlimit {
  uint64_t rlim_cur;
  uint64_t rlim_max;
};

#define SYS_prlimit64 ...

// mount stuff
#define SYS_mount ...
#define MS_BIND ...
#define MS_DIRSYNC ...
#define MS_LAZYTIME ...
#define MS_MANDLOCK ...
#define MS_MOVE ...
#define MS_NODEV ...
#define MS_NOEXEC ...
#define MS_NOSUID ...
#define MS_RDONLY ...
#define MS_REC ...
#define MS_RELATIME ...
#define MS_REMOUNT ...
#define MS_SILENT ...
#define MS_SLAVE ...
#define MS_STRICTATIME ...
#define MS_SYNCHRONOUS ...
#define MS_UNBINDABLE ...

#define SYS_umount2 ...
#define MNT_FORCE ...
#define MNT_DETACH ...
#define MNT_EXPIRE ...
#define UMOUNT_NOFOLLOW ...

//// FUSE stuff
// INIT flags
#define FUSE_ASYNC_READ		...
#define FUSE_POSIX_LOCKS	...
#define FUSE_FILE_OPS		...
#define FUSE_ATOMIC_O_TRUNC	...
#define FUSE_EXPORT_SUPPORT	...
#define FUSE_BIG_WRITES		...
#define FUSE_DONT_MASK		...
#define FUSE_SPLICE_WRITE	...
#define FUSE_SPLICE_MOVE	...
#define FUSE_SPLICE_READ	...
#define FUSE_FLOCK_LOCKS	...
#define FUSE_HAS_IOCTL_DIR	...
#define FUSE_AUTO_INVAL_DATA	...
#define FUSE_DO_READDIRPLUS	...
#define FUSE_READDIRPLUS_AUTO	...
#define FUSE_ASYNC_DIO		...
#define FUSE_WRITEBACK_CACHE	...
#define FUSE_NO_OPEN_SUPPORT	...
#define FUSE_PARALLEL_DIROPS    ...
#define FUSE_HANDLE_KILLPRIV	...
#define FUSE_POSIX_ACL		...
#define FUSE_ABORT_ERROR	...

// WRITE flags
#define FUSE_WRITE_CACHE	...
#define FUSE_WRITE_LOCKOWNER	...

// RELEASE flags
#define FUSE_RELEASE_FLUSH	...
#define FUSE_RELEASE_FLOCK_UNLOCK	...


enum fuse_opcode {
	FUSE_LOOKUP	   = ...,
	FUSE_FORGET	   = ..., /* no reply */
	FUSE_GETATTR	   = ...,
	FUSE_SETATTR	   = ...,
	FUSE_READLINK	   = ...,
	FUSE_SYMLINK	   = ...,
	FUSE_MKNOD	   = ...,
	FUSE_MKDIR	   = ...,
	FUSE_UNLINK	   = ...,
	FUSE_RMDIR	   = ...,
	FUSE_RENAME	   = ...,
	FUSE_LINK	   = ...,
	FUSE_OPEN	   = ...,
	FUSE_READ	   = ...,
	FUSE_WRITE	   = ...,
	FUSE_STATFS	   = ...,
	FUSE_RELEASE       = ...,
	FUSE_FSYNC         = ...,
	FUSE_SETXATTR      = ...,
	FUSE_GETXATTR      = ...,
	FUSE_LISTXATTR     = ...,
	FUSE_REMOVEXATTR   = ...,
	FUSE_FLUSH         = ...,
	FUSE_INIT          = ...,
	FUSE_OPENDIR       = ...,
	FUSE_READDIR       = ...,
	FUSE_RELEASEDIR    = ...,
	FUSE_FSYNCDIR      = ...,
	FUSE_GETLK         = ...,
	FUSE_SETLK         = ...,
	FUSE_SETLKW        = ...,
	FUSE_ACCESS        = ...,
	FUSE_CREATE        = ...,
	FUSE_INTERRUPT     = ...,
	FUSE_BMAP          = ...,
	FUSE_DESTROY       = ...,
	FUSE_IOCTL         = ...,
	FUSE_POLL          = ...,
	FUSE_NOTIFY_REPLY  = ...,
	FUSE_BATCH_FORGET  = ...,
	FUSE_FALLOCATE     = ...,
	FUSE_READDIRPLUS   = ...,
	FUSE_RENAME2       = ...,
	FUSE_LSEEK         = ...,

	/* CUSE specific operations */
	CUSE_INIT          = 4096,
};

struct fuse_attr {
	uint64_t	ino;
	uint64_t	size;
	uint64_t	blocks;
	uint64_t	atime;
	uint64_t	mtime;
	uint64_t	ctime;
	uint32_t	atimensec;
	uint32_t	mtimensec;
	uint32_t	ctimensec;
	uint32_t	mode;
	uint32_t	nlink;
	uint32_t	uid;
	uint32_t	gid;
	uint32_t	rdev;
	uint32_t	blksize;
	uint32_t	padding;
};

struct fuse_in_header {
    uint32_t len;       /* Total length of the data,
                           including this header */
    uint32_t opcode;    /* The kind of operation (see below) */
    uint64_t unique;    /* A unique identifier for this request */
    uint64_t nodeid;    /* ID of the filesystem object
                           being operated on */
    uint32_t uid;       /* UID of the requesting process */
    uint32_t gid;       /* GID of the requesting process */
    uint32_t pid;       /* PID of the requesting process */
    uint32_t padding;
};

struct fuse_out_header {
    uint32_t len;       /* Total length of data written to
                           the file descriptor */
    int32_t  error;     /* Any error that occurred (0 if none) */
    uint64_t unique;    /* The value from the
                           corresponding request */
};

struct fuse_init_in {
	uint32_t	major;
	uint32_t	minor;
	uint32_t	max_readahead;
	uint32_t	flags;
};

struct fuse_init_out {
	uint32_t	major;
	uint32_t	minor;
	uint32_t	max_readahead;
	uint32_t	flags;
	uint16_t	max_background;
	uint16_t	congestion_threshold;
	uint32_t	max_write;
	uint32_t	time_gran;
        ...;
};

struct fuse_open_in {
	uint32_t	flags;
	uint32_t	unused;
};

#define FOPEN_DIRECT_IO   ...
#define FOPEN_KEEP_CACHE  ...
#define FOPEN_NONSEEKABLE ...

struct fuse_open_out {
	uint64_t	fh;
	uint32_t	open_flags;
	uint32_t	padding;
};

struct fuse_entry_out {
	uint64_t	nodeid;		/* Inode ID */
	uint64_t	generation;	/* Inode generation: nodeid:gen must
					   be unique for the fs's lifetime */
	uint64_t	entry_valid;	/* Cache timeout for the name */
	uint64_t	attr_valid;	/* Cache timeout for the attributes */
	uint32_t	entry_valid_nsec;
	uint32_t	attr_valid_nsec;
	struct fuse_attr attr;
};

#define FUSE_READ_LOCKOWNER ...

struct fuse_read_in {
	uint64_t	fh;
	uint64_t	offset;
	uint32_t	size;
	uint32_t	read_flags;
	uint64_t	lock_owner;
	uint32_t	flags;
	uint32_t	padding;
};

#define FUSE_GETATTR_FH ...

struct fuse_getattr_in {
	uint32_t	getattr_flags;
	uint32_t	dummy;
	uint64_t	fh;
};

struct fuse_attr_out {
	uint64_t	attr_valid;	/* Cache timeout for the attributes */
	uint32_t	attr_valid_nsec;
	uint32_t	dummy;
	struct fuse_attr attr;
};

struct fuse_dirent {
	uint64_t	ino;
	uint64_t	off;
	uint32_t	namelen;
	uint32_t	type;
	char name[];
};

struct fuse_direntplus {
	struct fuse_entry_out entry_out;
	struct fuse_dirent dirent;
};

struct fuse_flush_in {
	uint64_t	fh;
	uint32_t	unused;
	uint32_t	padding;
	uint64_t	lock_owner;
};

struct fuse_release_in {
	uint64_t	fh;
	uint32_t	flags;
	uint32_t	release_flags;
	uint64_t	lock_owner;
};

struct fuse_getxattr_in {
	uint32_t	size;
	uint32_t	padding;
};

struct fuse_getxattr_out {
	uint32_t	size;
	uint32_t	padding;
};

// socket stuff
#define SYS_socket ...
#define SYS_socketpair ...
#define SYS_bind ...
#define SYS_listen ...
#define SYS_accept4 ...
#define SYS_connect ...
#define SYS_getsockname ...
#define SYS_getpeername ...
#define SYS_shutdown ...

#define SHUT_RD ...
#define SHUT_WR ...
#define SHUT_RDWR ...

#define SOCK_NONBLOCK ...
#define SOCK_CLOEXEC ...

#define SOCK_DGRAM ...
#define SOCK_STREAM ...
#define SOCK_SEQPACKET ...
#define SOCK_RAW ...

typedef unsigned... sa_family_t;

#define AF_UNIX ...
struct sockaddr_un {
    sa_family_t sun_family;               /* AF_UNIX */
    char        sun_path[108];            /* pathname */
};

#define AF_INET ...
typedef unsigned... in_port_t;
struct sockaddr_in {
    sa_family_t    sin_family; /* address family: AF_INET */
    in_port_t      sin_port;   /* port in network byte order */
    struct in_addr sin_addr;   /* internet address */
    ...;
};

#define AF_INET6 ...
struct sockaddr_in6 {
    sa_family_t     sin6_family; /* address family: AF_INET6 */
    in_port_t       sin6_port;   /* port in network byte order */
    uint32_t        sin6_flowinfo; /* IPv6 flow information */
    struct in6_addr sin6_addr;     /* IPv6 address */
    uint32_t        sin6_scope_id; /* Scope ID (new in 2.4) */
};

struct in6_addr {
    unsigned char   s6_addr[16];
    ...;
};

struct sockaddr {
    sa_family_t    sa_family;
    char sa_data[14];
};

struct sockaddr_storage {
    sa_family_t    ss_family;
    char _ss_padding[...];
};

/* Internet address. */
struct in_addr {
    uint32_t       s_addr;     /* address in network byte order */
};

#define IFNAMSIZ ...

struct ifmap {
    ...;
};

// low level networking
struct ifreq {
    char ifr_name[...]; /* Interface name */
    union {
        struct sockaddr ifr_addr;
        struct sockaddr ifr_dstaddr;
        struct sockaddr ifr_broadaddr;
        struct sockaddr ifr_netmask;
        struct sockaddr ifr_hwaddr;
        short           ifr_flags;
        int             ifr_ifindex;
        int             ifr_metric;
        int             ifr_mtu;
        struct ifmap    ifr_map;
        char            ifr_slave[...];
        char            ifr_newname[...];
        char           *ifr_data;
    };
};

#define TUNSETIFF ...
#define IFF_TUN ...
#define SIOCGIFINDEX ...

// netlink

#define AF_NETLINK ...
#define NETLINK_ROUTE ...
struct sockaddr_nl {
    sa_family_t     nl_family;  /* AF_NETLINK */
    unsigned short  nl_pad;     /* Zero */
    pid_t           nl_pid;     /* Port ID */
    uint32_t        nl_groups;  /* Multicast groups mask */
};


// sockopt stuff
#define SYS_getsockopt ...
#define SYS_setsockopt ...

#define SOL_SOCKET ...
#define SO_ACCEPTCONN ...
#define SO_ATTACH_FILTER ...
#define SO_ATTACH_BPF ...
#define SO_ATTACH_REUSEPORT_CBPF ...
#define SO_ATTACH_REUSEPORT_EBPF ...
#define SO_BINDTODEVICE ...
#define SO_BROADCAST ...
#define SO_BSDCOMPAT ...
#define SO_DEBUG ...
#define SO_DETACH_FILTER ...
#define SO_DETACH_BPF ...
#define SO_DOMAIN ...
#define SO_ERROR ...
#define SO_DONTROUTE ...
#define SO_INCOMING_CPU ...
#define SO_KEEPALIVE ...
#define SO_LINGER ...
#define SO_LOCK_FILTER ...
#define SO_MARK ...
#define SO_OOBINLINE ...
#define SO_PASSCRED ...
#define SO_PEEK_OFF ...
#define SO_PEERCRED ...
#define SO_PRIORITY ...
#define SO_PROTOCOL ...
#define SO_RCVBUF ...
#define SO_RCVBUFFORCE ...
#define SO_RCVLOWAT ...
#define SO_SNDLOWAT ...
#define SO_RCVTIMEO ...
#define SO_SNDTIMEO ...
#define SO_REUSEADDR ...
#define SO_REUSEPORT ...
#define SO_RXQ_OVFL ...
#define SO_SNDBUF ...
#define SO_SNDBUFFORCE ...
#define SO_TIMESTAMP ...
#define SO_TYPE ...
#define SO_BUSY_POLL ...

#define SOL_IP ...

#define IPPROTO_ICMPV6 ...

#define IP_RECVERR ...
#define IP_PKTINFO ...
#define IP_MULTICAST_TTL ...
#define IP_MTU_DISCOVER ...
#define IP_PMTUDISC_DONT ...

// fcntl stuff
#define SYS_fcntl ...

#define F_SETFD ...
#define F_GETFD ...
#define F_SETFL ...

#define FD_CLOEXEC ...

// mmap stuff
#define SYS_mmap ...
#define SYS_munmap ...
#define SYS_memfd_create ...

#define MFD_CLOEXEC ...

#define PROT_EXEC ...
#define PROT_READ ...
#define PROT_WRITE ...
#define PROT_NONE ...

#define MAP_SHARED ...
#define MAP_ANONYMOUS ...
#define MAP_PRIVATE ...
#define MAP_POPULATE ...
#define MAP_GROWSDOWN ...
#define MAP_STACK ...

void *memcpy(void *dest, const void *src, size_t n);
// we need these as function pointers, we aren't calling them from Python
int (*const rsyscall_persistent_server)(int infd, int outfd, const int listensock);
int (*const rsyscall_server)(const int infd, const int outfd);
void (*const rsyscall_futex_helper)(void *futex_addr);
void (*const rsyscall_trampoline)(void);

struct rsyscall_trampoline_stack {
    int64_t rdi;
    int64_t rsi;
    int64_t rdx;
    int64_t rcx;
    int64_t r8;
    int64_t r9;
    void* function;
};

struct rsyscall_syscall {
    int64_t sys;
    int64_t args[6];
};
struct rsyscall_symbol_table {
    void* rsyscall_server;
    void* rsyscall_persistent_server;
    void* rsyscall_futex_helper;
    void* rsyscall_trampoline;
};
struct rsyscall_bootstrap {
    struct rsyscall_symbol_table symbols;
    pid_t pid;
    int listening_sock;
    int syscall_sock;
    int data_sock;
    size_t envp_count;
};
struct rsyscall_stdin_bootstrap {
    struct rsyscall_symbol_table symbols;
    pid_t pid;
    int syscall_fd;
    int data_fd;
    int futex_memfd;
    int connecting_fd;
    size_t envp_count;
};
struct rsyscall_unix_stub {
    struct rsyscall_symbol_table symbols;
    pid_t pid;
    int syscall_fd;
    int data_fd;
    int futex_memfd;
    int connecting_fd;
    size_t argc;
    size_t envp_count;
    uint64_t sigmask;
};

// sockets
#define SYS_sendto ...
#define SYS_recvfrom ...

#define SYS_sendmsg ...
#define SYS_recvmsg ...

#define SCM_RIGHTS ...

// send flags
#define MSG_CONFIRM ...
#define MSG_DONTROUTE ...
#define MSG_EOR ...
#define MSG_MORE ...
#define MSG_NOSIGNAL ...
// recv flags
#define MSG_CMSG_CLOEXEC ...
#define MSG_ERRQUEUE ...
#define MSG_PEEK ...
#define MSG_TRUNC ...
#define MSG_WAITALL ...
// both
#define MSG_DONTWAIT ...
#define MSG_OOB ...
// recvmsg returned msg_flags field
#define MSG_CTRUNC ...

struct iovec {
    void *iov_base;	/* Pointer to data.  */
    size_t iov_len;	/* Length of data.  */
};

typedef unsigned long int socklen_t;
struct msghdr {
    void *msg_name;		/* Address to send to/receive from.  */
    int msg_namelen;     	/* Length of address data.  */
  
    struct iovec *msg_iov;	/* Vector of data to send/receive into.  */
    unsigned long int msg_iovlen;		/* Number of elements in the vector.  */
  
    void *msg_control;		/* Ancillary data (eg BSD filedesc passing). */
    unsigned long int msg_controllen;	/* Ancillary data buffer length.  */
  
    int msg_flags;		/* Flags in received message.  */
};

struct cmsghdr {
    unsigned long int cmsg_len;		/* Length of data in cmsg_data plus length
				   of cmsghdr structure.  */
    int cmsg_level;		/* Originating protocol.  */
    int cmsg_type;		/* Protocol specific type.  */
    ...;
};

//// ugh, have to take this over to get notification of thread exec
#define SYS_set_robust_list ...

// see kernel source for documentation of robust_lists.
// this is our custom robust_list + futex structure
struct futex_node {
  struct robust_list list;
  uint32_t futex;
};

struct robust_list {
  struct robust_list *next;
};

struct robust_list_head {
  struct robust_list list;
  unsigned long futex_offset;
  struct robust_list *list_op_pending;
};

#define FUTEX_WAITERS ...
#define FUTEX_TID_MASK ...

""")
