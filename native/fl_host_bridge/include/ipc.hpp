#pragma once

#include "wire.hpp"
#include <windows.h>
#include <atomic>
#include <memory>
#include <mutex>
#include <thread>

namespace alx {
class Handle {
public:
    explicit Handle(HANDLE value = nullptr) : value_(value) {}
    ~Handle() { if (value_ && value_ != INVALID_HANDLE_VALUE) CloseHandle(value_); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    HANDLE get() const { return value_; }
private:
    HANDLE value_;
};

struct Job {
    Request request;
    Response response;
    Handle done{CreateEventW(nullptr, TRUE, FALSE, nullptr)};
    std::atomic<bool> cancelled{false};
};

// The pipe worker only transfers fixed bytes. The plugin GUI thread owns execution.
class Server {
public:
    Server();
    ~Server();
    Server(const Server&) = delete;
    Server& operator=(const Server&) = delete;
    std::shared_ptr<Job> take();
    void complete(const std::shared_ptr<Job>& job);
private:
    void run();
    Handle stop_{CreateEventW(nullptr, TRUE, FALSE, nullptr)};
    Handle pipe_;
    std::mutex mutex_;
    std::shared_ptr<Job> pending_;
    std::thread worker_;
};

Response call(std::uint32_t pid, const Request& request, std::uint32_t timeout_ms = 3000);
} // namespace alx
