package updates

import (
	"errors"
	"strconv"
	"strings"
)

func releaseVersion(version string) ([3]int, error) {
	var parts [3]int
	base := strings.SplitN(strings.SplitN(version, "-", 2)[0], "+", 2)[0]
	fields := strings.Split(base, ".")
	if len(fields) != len(parts) {
		return parts, errors.New("invalid release version")
	}
	for i, value := range fields {
		number, err := strconv.Atoi(value)
		if err != nil || number < 0 {
			return parts, errors.New("invalid release version")
		}
		parts[i] = number
	}
	return parts, nil
}

// NewerVersion requires a strictly greater three-part application version.
func NewerVersion(current, next string) bool {
	before, err := releaseVersion(current)
	if err != nil {
		return false
	}
	after, err := releaseVersion(next)
	if err != nil {
		return false
	}
	for i := range before {
		if after[i] != before[i] {
			return after[i] > before[i]
		}
	}
	return false
}
