/* Read exported pstore files without modifying their source. See PSTORE-SNAPSHOT.md. */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define PSTORE_MAGIC 0x6165676c
#define TMPFS_MAGIC 0x01021994
#define MAX_FILES 32
#define MAX_FILE (4U * 1024 * 1024)
#define MAX_TOTAL (8U * 1024 * 1024)

struct sha256 {
    uint32_t h[8];
    uint64_t bytes;
    unsigned char block[64];
    size_t used;
};

static uint32_t ror(uint32_t v, unsigned n) { return (v >> n) | (v << (32 - n)); }

static void sha_block(struct sha256 *s) {
    static const uint32_t k[64] = {
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
    };
    uint32_t w[64];
    for (unsigned i = 0; i < 16; ++i) {
        const unsigned char *p = s->block + 4 * i;
        w[i] = (uint32_t)p[0]<<24 | (uint32_t)p[1]<<16 | (uint32_t)p[2]<<8 | p[3];
    }
    for (unsigned i = 16; i < 64; ++i) {
        uint32_t a = w[i-15], b = w[i-2];
        w[i] = w[i-16] + (ror(a,7)^ror(a,18)^(a>>3)) + w[i-7]
             + (ror(b,17)^ror(b,19)^(b>>10));
    }
    uint32_t a=s->h[0], b=s->h[1], c=s->h[2], d=s->h[3];
    uint32_t e=s->h[4], f=s->h[5], g=s->h[6], h=s->h[7];
    for (unsigned i = 0; i < 64; ++i) {
        uint32_t t = h + (ror(e,6)^ror(e,11)^ror(e,25)) + ((e&f)^(~e&g)) + k[i] + w[i];
        uint32_t u = (ror(a,2)^ror(a,13)^ror(a,22)) + ((a&b)^(a&c)^(b&c));
        h=g; g=f; f=e; e=d+t; d=c; c=b; b=a; a=t+u;
    }
    s->h[0]+=a; s->h[1]+=b; s->h[2]+=c; s->h[3]+=d;
    s->h[4]+=e; s->h[5]+=f; s->h[6]+=g; s->h[7]+=h;
}

static void sha_init(struct sha256 *s) {
    *s = (struct sha256){.h={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
                                            0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19}};
}

static void sha_update(struct sha256 *s, const unsigned char *p, size_t n) {
    s->bytes += n;
    while (n) {
        size_t take = 64 - s->used;
        if (take > n) take = n;
        memcpy(s->block + s->used, p, take);
        s->used += take; p += take; n -= take;
        if (s->used == 64) { sha_block(s); s->used = 0; }
    }
}

static void sha_finish(struct sha256 *s, char hex[65]) {
    uint64_t bits = s->bytes * 8;
    unsigned char one = 0x80, zero = 0;
    sha_update(s, &one, 1);
    while (s->used != 56) sha_update(s, &zero, 1);
    unsigned char tail[8];
    for (unsigned i = 0; i < 8; ++i) tail[7-i] = (unsigned char)(bits >> (8*i));
    sha_update(s, tail, 8);
    for (unsigned i = 0; i < 8; ++i) snprintf(hex + 8*i, 9, "%08x", s->h[i]);
}

static uint64_t monotonic_ms(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) _exit(125);
    return (uint64_t)t.tv_sec * 1000 + (uint64_t)t.tv_nsec / 1000000;
}

/* Walk every component, including ancestors, without following symlinks. */
static int directory(const char *path) {
    if (!path || path[0] != '/' || strlen(path) >= 4096) { errno=EINVAL; return -1; }
    char copy[4096];
    strcpy(copy, path);
    int fd = open("/", O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
    if (fd < 0) return -1;
    char *save = NULL;
    for (char *p = strtok_r(copy, "/", &save); p; p = strtok_r(NULL, "/", &save)) {
        if (!strcmp(p,".") || !strcmp(p,"..")) { close(fd); errno=EINVAL; return -1; }
        int next = openat(fd, p, O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
        int error = errno;
        close(fd);
        if (next < 0) { errno=error; return -1; }
        fd = next;
    }
    return fd;
}

static int new_file(int dir, const char *name) {
    return openat(dir, name, O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW, 0600);
}

static int write_all(int fd, const void *data, size_t n) {
    const unsigned char *p = data;
    while (n) {
        ssize_t done = write(fd, p, n);
        if (done < 0 && errno == EINTR) continue;
        if (done <= 0) return -1;
        p += done; n -= (size_t)done;
    }
    return 0;
}

static int metadata(int out, const char *source, const char *name, size_t limit) {
    int input = open(source, O_RDONLY|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK);
    if (input < 0) return -1;
    int dest = new_file(out, name);
    if (dest < 0) { close(input); return -1; }
    unsigned char buffer[4096];
    size_t total = 0;
    int result = 0;
    for (;;) {
        ssize_t n = read(input, buffer, sizeof(buffer));
        if (n < 0 && errno == EINTR) continue;
        if (n == 0) break;
        if (n < 0 || total + (size_t)n > limit || write_all(dest, buffer, (size_t)n)) {
            result = -1; break;
        }
        total += (size_t)n;
    }
    if (!total) result = -1;
    if (close(dest)) result = -1;
    close(input);
    return result;
}

static void hex_name(FILE *report, const char *name) {
    for (const unsigned char *p = (const unsigned char *)name; *p; ++p) fprintf(report, "%02x", *p);
}

static int unchanged(const struct stat *a, const struct stat *b) {
    return a->st_dev==b->st_dev && a->st_ino==b->st_ino && a->st_size==b->st_size
        && a->st_mtim.tv_sec==b->st_mtim.tv_sec && a->st_mtim.tv_nsec==b->st_mtim.tv_nsec
        && a->st_ctim.tv_sec==b->st_ctim.tv_sec && a->st_ctim.tv_nsec==b->st_ctim.tv_nsec;
}

static int collect(int out, const char *source) {
    int reportfd = new_file(out, "manifest.jsonl");
    if (reportfd < 0) return 1;
    FILE *report = fdopen(reportfd, "w");
    if (!report) { close(reportfd); return 1; }
    setvbuf(report, NULL, _IONBF, 0);
    fprintf(report, "{\"event\":\"begin\",\"monotonic_ms\":%llu}\n", (unsigned long long)monotonic_ms());
    int failed = 0;
    if (metadata(out, "/proc/self/mountinfo", "mountinfo", 256*1024)) {
        fprintf(report, "{\"event\":\"metadata_error\",\"file\":\"mountinfo\"}\n"); failed = 1;
    }
    if (metadata(out, "/proc/sys/kernel/random/boot_id", "boot_id", 128)) {
        fprintf(report, "{\"event\":\"metadata_error\",\"file\":\"boot_id\"}\n"); failed = 1;
    }
    int src = directory(source);
    if (src < 0) {
        fprintf(report, "{\"event\":\"source_open_error\",\"errno\":%d}\n", errno);
        fclose(report); return 1;
    }
    struct statfs fs;
    if (fstatfs(src, &fs)) { close(src); fclose(report); return 1; }
    fprintf(report, "{\"event\":\"source_fs\",\"magic\":%llu}\n", (unsigned long long)fs.f_type);
#ifndef SNAPSHOT_TESTING
    if ((unsigned long)fs.f_type != PSTORE_MAGIC) {
        fprintf(report, "{\"event\":\"wrong_source_fs\"}\n"); close(src); fclose(report); return 1;
    }
#endif
    DIR *dir = fdopendir(src);
    if (!dir) {
        fprintf(report, "{\"event\":\"enumeration_error\",\"errno\":%d}\n", errno);
        close(src); fclose(report); return 1;
    }
    unsigned count = 0;
    size_t total = 0;
    for (;;) {
        errno = 0;
        struct dirent *entry = readdir(dir);
        if (!entry) {
            if (errno) { fprintf(report,"{\"event\":\"enumeration_error\",\"errno\":%d}\n",errno); failed=1; }
            break;
        }
        if (!strcmp(entry->d_name,".") || !strcmp(entry->d_name,"..")) continue;
        if (count == MAX_FILES) { fprintf(report,"{\"event\":\"file_count_limit\"}\n"); failed=1; break; }
        char destname[32];
        snprintf(destname, sizeof(destname), "record-%03u.bin", count++);
        int input = -1, dest = -1, error = 0;
        const char *status = "ok";
        size_t bytes = 0;
        struct sha256 sha; sha_init(&sha);
        struct stat before, after, linked;
        input = openat(src, entry->d_name, O_RDONLY|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK);
        if (input < 0) { status="open_error"; error=errno; }
        else if (fstat(input, &before)) { status="stat_error"; error=errno; }
        else if (!S_ISREG(before.st_mode)) status="nonregular";
        else if (before.st_size < 0 || (uint64_t)before.st_size > MAX_FILE) status="file_size_limit";
        else if ((uint64_t)before.st_size > MAX_TOTAL-total) status="total_size_limit";
        else {
            dest = new_file(out, destname);
            if (dest < 0) { status="output_error"; error=errno; }
            else {
                unsigned char buffer[65536];
                for (;;) {
                    ssize_t n = read(input, buffer, sizeof(buffer));
                    if (n < 0 && errno == EINTR) continue;
                    if (n == 0) break;
                    if (n < 0) { status="read_error"; error=errno; break; }
                    if (bytes+(size_t)n > MAX_FILE) { status="file_size_limit"; break; }
                    if (total+(size_t)n > MAX_TOTAL) { status="total_size_limit"; break; }
                    size_t offset = 0;
                    while (offset < (size_t)n) {
                        ssize_t written = write(dest,buffer+offset,(size_t)n-offset);
                        if (written < 0 && errno == EINTR) continue;
                        if (written <= 0) {
                            status="write_error"; error=written<0?errno:EIO; break;
                        }
                        sha_update(&sha,buffer+offset,(size_t)written);
                        offset += (size_t)written;
                        bytes += (size_t)written; total += (size_t)written;
                    }
                    if (strcmp(status,"ok")) break;
                }
                if (close(dest) && !strcmp(status,"ok")) { status="close_error"; error=errno; }
                if (!strcmp(status,"ok")) {
                    if (fstat(input,&after) || fstatat(src,entry->d_name,&linked,AT_SYMLINK_NOFOLLOW)) {
                        status="restat_error"; error=errno;
                    } else if (!unchanged(&before,&after) || !unchanged(&after,&linked)) status="source_changed";
                }
            }
        }
        if (input >= 0) close(input);
        char digest[65]; sha_finish(&sha,digest);
        fprintf(report,"{\"event\":\"file\",\"name_hex\":\""); hex_name(report,entry->d_name);
        fprintf(report,"\",\"stored\":");
        if (dest >= 0) fprintf(report,"\"%s\"",destname); else fprintf(report,"null");
        fprintf(report,",\"bytes\":%zu,\"sha256\":\"%s\",\"status\":\"%s\",\"errno\":%d}\n",bytes,digest,status,error);
        if (strcmp(status,"ok")) failed = 1;
    }
    closedir(dir);
    fprintf(report,"{\"event\":\"end\",\"complete\":%s,\"entries\":%u,\"bytes\":%zu,\"monotonic_ms\":%llu}\n",
            failed?"false":"true",count,total,(unsigned long long)monotonic_ms());
    if (ferror(report)) failed=1;
    if (fclose(report)) failed=1;
    return failed;
}

int main(int argc, char **argv) {
    uint64_t start = monotonic_ms(), timeout = 5000;
    const char *source = "/sys/fs/pstore";
    const char *outpath = NULL;
#ifdef SNAPSHOT_TESTING
    unsigned delay_ms = 0;
#endif
    for (int i=1; i<argc; ++i) {
        if (!strcmp(argv[i],"--out") && i+1<argc && !outpath) outpath=argv[++i];
#ifdef SNAPSHOT_TESTING
        else if (!strcmp(argv[i],"--test-source") && i+1<argc) source=argv[++i];
        else if (!strcmp(argv[i],"--test-delay-ms") && i+1<argc) delay_ms=(unsigned)strtoul(argv[++i],NULL,10);
        else if (!strcmp(argv[i],"--test-timeout-ms") && i+1<argc) timeout=strtoul(argv[++i],NULL,10);
#endif
        else return 2;
    }
    if (!outpath || strlen(outpath)>=4096 || outpath[0]!='/' || !timeout) return 2;
    char path[4096]; strcpy(path,outpath);
    char *name = strrchr(path,'/');
    if (!name || !name[1] || !strcmp(name+1,".") || !strcmp(name+1,"..")) return 2;
    *name++ = 0;
#ifndef SNAPSHOT_TESTING
    if (strcmp(path,"/dev")) return 2;
#endif
    umask(077);
    int parent = directory(path[0]?path:"/");
    if (parent<0) return 1;
#ifndef SNAPSHOT_TESTING
    struct statfs fs;
    if (fstatfs(parent,&fs) || (unsigned long)fs.f_type!=TMPFS_MAGIC || geteuid()!=0) { close(parent); return 1; }
#endif
    if (mkdirat(parent,name,0700)) { close(parent); return 1; }
    int out = openat(parent,name,O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
    close(parent);
    if (out<0) return 1;
    int statusfd = new_file(out,"status.json");
    if (statusfd<0) { close(out); return 1; }
    pid_t owner = getpid(), child = fork();
    if (child==0) {
        close(statusfd);
        if (prctl(PR_SET_PDEATHSIG,SIGKILL) || getppid()!=owner) _exit(1);
#ifdef SNAPSHOT_TESTING
        struct timespec delay = {.tv_sec=delay_ms/1000,.tv_nsec=(long)(delay_ms%1000)*1000000};
        while (nanosleep(&delay,&delay) && errno==EINTR) {}
#endif
        _exit(collect(out,source));
    }
    const char *state = "fork_error";
    int complete = 0, code = -1;
    if (child>0) {
        for (;;) {
            int status;
            pid_t done = waitpid(child,&status,WNOHANG);
            if (done==child) {
                if (WIFEXITED(status)) code=WEXITSTATUS(status);
                complete = code==0;
                state = complete?"complete":"incomplete";
                break;
            }
            if (done<0 && errno!=EINTR) { state="wait_error"; kill(child,SIGKILL); break; }
            if (monotonic_ms()-start>=timeout) {
                state="timeout"; kill(child,SIGKILL);
                /* Never block init waiting for an uninterruptible child. */
                waitpid(child,NULL,WNOHANG);
                break;
            }
            struct timespec pause = {.tv_nsec=10000000}; nanosleep(&pause,NULL);
        }
    }
    char status[512];
    int n = snprintf(status,sizeof(status),"{\"schema_version\":1,\"state\":\"%s\",\"complete\":%s,\"child_exit\":%d,\"started_monotonic_ms\":%llu,\"finished_monotonic_ms\":%llu}\n",
                     state,complete?"true":"false",code,(unsigned long long)start,(unsigned long long)monotonic_ms());
    int failed = n<0 || (size_t)n>=sizeof(status) || write_all(statusfd,status,(size_t)n);
    if (close(statusfd)) failed=1;
    close(out);
    return complete && !failed ? 0 : 1;
}
