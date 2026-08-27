cmake_minimum_required(VERSION 3.22.1)
project(aichat_native)

add_library(aichat_native SHARED native_marker.cpp)
find_library(log_lib log)
target_link_libraries(aichat_native ${log_lib})
