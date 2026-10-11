package main

import (
	"net"
	"testing"
)

func TestAvailableLocalPortAvoidsOccupiedService(t *testing.T) {
	occupied, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer occupied.Close()
	preferred := occupied.Addr().(*net.TCPAddr).Port
	got, err := availableLocalPort(preferred)
	if err != nil {
		t.Fatal(err)
	}
	if got == preferred || got == 0 {
		t.Fatalf("occupied port %d must not be reused; got %d", preferred, got)
	}
	listener, err := net.Listen("tcp4", net.JoinHostPort("127.0.0.1", "0"))
	if err != nil {
		t.Fatal(err)
	}
	free := listener.Addr().(*net.TCPAddr).Port
	listener.Close()
	got, err = availableLocalPort(free)
	if err != nil || got != free {
		t.Fatalf("available configured port should be kept: got %d, err %v", got, err)
	}
}
