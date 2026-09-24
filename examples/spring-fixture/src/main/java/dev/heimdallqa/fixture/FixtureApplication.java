package dev.heimdallqa.fixture;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * The smallest Spring Boot application that still answers every axis the harness
 * asks about, so a person can see the reader's numbers checked against a running
 * API instead of against our reading of it.
 */
@SpringBootApplication
public class FixtureApplication {

    public static void main(String[] args) {
        SpringApplication.run(FixtureApplication.class, args);
    }
}
